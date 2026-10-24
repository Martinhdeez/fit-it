"""Trace sink. Producers call emit and nothing else.

The journal is one JSON object per line under FITIT_TRACE_DIR. seq is assigned
when the line is committed, so cursor order matches the file. One process
writes a directory. flush is true only when the records accepted before that
call are on disk.
"""

import collections
try:
    import fcntl
except ImportError:  # the core image is Linux; a Windows host has no flock
    fcntl = None
import hashlib
import json
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

from opentelemetry import trace as otel_trace
from opentelemetry.context import get_current
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.semconv.attributes.error_attributes import ERROR_TYPE
from opentelemetry.semconv.attributes.http_attributes import HTTP_REQUEST_METHOD, HTTP_RESPONSE_STATUS_CODE
from opentelemetry.semconv.attributes.service_attributes import SERVICE_NAME
from opentelemetry.semconv.attributes.url_attributes import URL_PATH, URL_QUERY
from opentelemetry.trace import ProxyTracerProvider, SpanKind, Status, StatusCode, format_span_id, format_trace_id

_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
_QUEUE_MAX = 10000
_SENSITIVE = ("text", "input", "diff", "reason", "error", "logs")

_queue: queue.Queue = queue.Queue(maxsize=_QUEUE_MAX)
_cond = threading.Condition()
_worker_started = False
_closed = False
_inflight: list[dict[str, Any]] = []
_next_seq: dict[str, int] = {}
_bound: set[str] = set()
_lock_fds: list[int] = []
# _hold pauses the writer so tests can fill the queue. Not used in production.

_accepted = 0
_persisted = 0
_failed = 0
_dropped = 0
_exporter_failed = 0
_messages: collections.deque[str] = collections.deque(maxlen=100)
_journal_problems: list[str] = []
_provider_ready = False
_otlp_added = False
_added_exporters: set[int] = set()

_hold = threading.Event()
_hold.set()
_in_hold = threading.Event()


class _SpanView:
    """Exported view of a span with query strings and status text removed."""

    def __init__(self, span) -> None:
        self._span = span

    def __getattr__(self, name: str):
        return getattr(self._span, name)

    @property
    def attributes(self):
        raw = dict(self._span.attributes or {})
        if URL_QUERY in raw:
            raw[URL_QUERY] = "REDACTED"
        path = raw.get(URL_PATH)
        if isinstance(path, str):
            raw[URL_PATH] = _template_path(path)
        return raw

    @property
    def status(self):
        status = self._span.status
        if status is not None and getattr(status, "description", None):
            return Status(status.status_code)
        return status


class _RedactingExporter:
    def __init__(self, inner) -> None:
        self.inner = inner

    def export(self, spans):
        return self.inner.export([_SpanView(span) for span in spans])

    def shutdown(self):
        return self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000):
        return self.inner.force_flush(timeout_millis)


def emit(kind: str, **fields) -> None:
    try:
        kind_s = kind if isinstance(kind, str) else str(kind)
        body = {}
        for key, value in fields.items():
            name = str(key)
            if name in ("ts", "kind", "seq", "trace_id", "parent_span_id"):
                continue
            body[name] = _freeze(value, set())
        span_context = otel_trace.get_current_span().get_span_context()
        parent = None
        if span_context is not None and span_context.is_valid:
            parent = (format_trace_id(span_context.trace_id), format_span_id(span_context.span_id))
        ticket: dict[str, Any] = {"done": False, "ok": False}
        item = {
            "kind": kind_s,
            "body": body,
            "ts": time.time(),
            "emitted_ns": time.time_ns(),
            "context": get_current(),
            "parent": parent,
            "directory": _trace_dir(),
            "ticket": ticket,
        }
        queued = False
        with _cond:
            global _accepted
            if _closed:
                _drop("sink closed")
                return
            _inflight.append(ticket)
        try:
            _queue.put_nowait(item)
            queued = True
        except queue.Full:
            with _cond:
                global _dropped
                _dropped += 1
            _note("audit queue full")
        finally:
            if not queued:
                _discard_ticket(ticket)
                return
        with _cond:
            _accepted += 1
        _ensure_worker()
    except Exception as exc:
        _note(f"emit failed: {type(exc).__name__}")


def flush(timeout_ms: int = 5000) -> bool:
    """True only if every record accepted before this call reached the journal."""
    return _wait_journal(timeout_ms)


def shutdown(timeout_ms: int = 5000) -> bool:
    """Stop intake, finish accepted records, then flush the exporter with the time left."""
    global _closed
    deadline = time.monotonic() + max(timeout_ms, 0) / 1000
    with _cond:
        _closed = True
    remaining = max(0, int((deadline - time.monotonic()) * 1000))
    ok = _wait_journal(remaining)
    left = max(0, int((deadline - time.monotonic()) * 1000))
    _flush_exporter(left)
    return ok


def _wait_journal(timeout_ms: int) -> bool:
    deadline = time.monotonic() + max(timeout_ms, 0) / 1000
    with _cond:
        waiting = [ticket for ticket in _inflight]
    while True:
        with _cond:
            if all(ticket["done"] for ticket in waiting):
                return all(ticket["ok"] for ticket in waiting)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _cond.wait(remaining)


def startup() -> None:
    """Allow intake again after shutdown. The database startup stays in the app lifespan."""
    global _closed
    with _cond:
        _closed = False
    configure()


def read_events(*, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    """Committed records with seq > after, in cursor order. limit is capped at 1000."""
    path = _trace_dir() / "events.jsonl"
    limit = max(0, min(int(limit), 1000))
    problems: list[str] = []
    chosen: list[dict[str, Any]] = []
    try:
        handle = path.open("rb")
    except FileNotFoundError:
        _set_problems([])
        return []
    except OSError as exc:
        _set_problems([f"read: {type(exc).__name__}"])
        return []
    with handle:
        for number, raw_line in enumerate(handle, 1):
            if len(chosen) >= limit:
                break
            line = raw_line[:-1] if raw_line.endswith(b"\n") else raw_line
            if not line.strip():
                if not raw_line.endswith(b"\n") and line:
                    problems.append("incomplete tail")
                continue
            record, problem = _parse_record(line)
            if problem:
                problems.append(f"line {number}: {problem}")
                if not raw_line.endswith(b"\n"):
                    problems.append("incomplete tail")
                continue
            if record["seq"] > after:
                chosen.append(record)
    _set_problems(problems)
    return chosen


def _parse_record(line: bytes) -> tuple[dict[str, Any] | None, str | None]:
    try:
        record = json.loads(line)
    except ValueError:
        return None, "invalid"
    if not isinstance(record, dict) or not isinstance(record.get("seq"), int) or isinstance(record.get("seq"), bool):
        return None, "missing seq"
    return record, None


def diagnostics() -> dict[str, Any]:
    with _cond:
        return {
            "accepted": _accepted,
            "persisted": _persisted,
            "pending": sum(1 for ticket in _inflight if not ticket["done"]),
            "failed": _failed,
            "dropped": _dropped,
            "exporter_failed": _exporter_failed,
            "messages": list(_messages),
            "journal": list(_journal_problems),
        }


def configure(exporter=None) -> None:
    """One provider for FastAPI and for emit. Query strings are redacted on the way out."""
    global _provider_ready, _otlp_added
    provider = otel_trace.get_tracer_provider()
    if isinstance(provider, ProxyTracerProvider):
        provider = TracerProvider(resource=Resource.create({
            SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", "fit-it-core"),
        }))
        otel_trace.set_tracer_provider(provider)
    if not isinstance(provider, TracerProvider):
        _provider_ready = True
        return
    if exporter is not None and id(exporter) not in _added_exporters:
        provider.add_span_processor(SimpleSpanProcessor(_RedactingExporter(exporter)))
        _added_exporters.add(id(exporter))
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    if endpoint and not _otlp_added and exporter is None:
        provider.add_span_processor(BatchSpanProcessor(_RedactingExporter(OTLPSpanExporter())))
        _otlp_added = True
    _provider_ready = True


def _trace_dir() -> Path:
    override = os.environ.get("FITIT_TRACE_DIR")
    if override:
        return Path(override)
    return Path(".fitit-trace")


def _freeze(value: Any, seen: set[int]) -> Any:
    try:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            if id(value) in seen:
                return "<cycle>"
            seen.add(id(value))
            try:
                return {str(key): _freeze(item, seen) for key, item in value.items()}
            finally:
                seen.discard(id(value))
        if isinstance(value, (list, tuple, set)):
            if id(value) in seen:
                return "<cycle>"
            seen.add(id(value))
            try:
                return [_freeze(item, seen) for item in value]
            finally:
                seen.discard(id(value))
        return str(value)
    except Exception:
        return "<unrepresentable>"


def _ensure_worker() -> None:
    global _worker_started
    with _cond:
        if _worker_started:
            return
        _worker_started = True
    threading.Thread(target=_loop, name="fit-it-trace", daemon=True).start()


def _loop() -> None:
    while True:
        batch = [_queue.get()]
        try:
            if not _hold.is_set():
                _in_hold.set()
                _hold.wait()
                _in_hold.clear()
            while True:
                try:
                    batch.append(_queue.get_nowait())
                except queue.Empty:
                    break
            _handle_batch(batch)
        except Exception as exc:
            for item in batch:
                _finish(item, False)
            _note(f"writer failed: {type(exc).__name__}")
        finally:
            for _item in batch:
                _queue.task_done()


def _handle_batch(batch: list[dict[str, Any]]) -> None:
    grouped: dict[Path, list[dict[str, Any]]] = {}
    for item in batch:
        grouped.setdefault(item["directory"], []).append(item)
    for directory, items in grouped.items():
        try:
            directory = directory.resolve()
            _bind(directory)
            records = []
            for item in items:
                seq = _next_seq[str(directory)]
                _next_seq[str(directory)] = seq + 1
                record = {"ts": item["ts"], "kind": item["kind"], **item["body"], "seq": seq}
                if item["parent"] is not None:
                    record["trace_id"] = item["parent"][0]
                    record["parent_span_id"] = item["parent"][1]
                item["record"] = record
                records.append(json.dumps(record, separators=(",", ":")))
            _append(directory / "events.jsonl", records)
        except Exception as exc:
            _note(f"journal: {type(exc).__name__}")
            for item in items:
                _finish(item, False)
            continue
        for item, line in zip(items, records):
            try:
                _write_stdout(line)
            except Exception as exc:
                _note(f"stdout: {type(exc).__name__}")
            try:
                _export_span(item)
            except Exception as exc:
                _note(f"export: {type(exc).__name__}")
            _finish(item, True)


def _finish(item: dict[str, Any], ok: bool) -> None:
    global _persisted, _failed
    with _cond:
        if item.get("settled"):
            return
        item["settled"] = True
        if ok:
            _persisted += 1
        else:
            _failed += 1
        ticket = item["ticket"]
        ticket["ok"] = ok
        ticket["done"] = True
        _inflight[:] = [existing for existing in _inflight if existing is not ticket]
        _cond.notify_all()


def _bind(directory: Path) -> None:
    directory = directory.resolve()
    key = str(directory)
    if key in _bound:
        return
    directory.mkdir(parents=True, exist_ok=True)
    _acquire(directory)
    _recover(directory)
    _bound.add(key)


def _acquire(directory: Path) -> None:
    if fcntl is None:
        return
    lock = directory / "writer.lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise RuntimeError("another trace writer holds this directory")
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    _lock_fds.append(fd)


def _recover(directory: Path) -> None:
    path = directory / "events.jsonl"
    if not path.exists() or path.stat().st_size == 0:
        _next_seq[str(directory)] = 1
        return
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        head, sep, tail = raw.rpartition(b"\n")
        try:
            json.loads(tail)
            valid = True
        except ValueError:
            valid = False
        if valid:
            with path.open("ab") as handle:
                handle.write(b"\n")
                handle.flush()
                os.fsync(handle.fileno())
        else:
            with (directory / "events.corrupt").open("ab") as handle:
                handle.write(tail)
                handle.flush()
                os.fsync(handle.fileno())
            offset = len(head) + len(sep)
            os.truncate(path, offset)
            _note(f"isolated incomplete tail at byte {offset}")
            raw = path.read_bytes()
    highest = 0
    saw = False
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        record, _problem = _parse_record(line)
        if record is None:
            continue
        highest = max(highest, record["seq"])
        saw = True
    if not saw:
        raise RuntimeError("journal has no readable cursor")
    _next_seq[str(directory)] = highest + 1


def _append(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _write_stdout(line: str) -> None:
    record = json.loads(line)
    print(f"{record.get('seq')} {record.get('kind')} {record.get('user')}", flush=True)


def _export_span(item: dict[str, Any]) -> None:
    record = item["record"]
    when = item["emitted_ns"]
    tracer = otel_trace.get_tracer("fit-it.trace")
    parent = item["context"] if item["parent"] is not None else None
    span = tracer.start_span(str(record["kind"]), context=parent, kind=SpanKind.INTERNAL, start_time=when)
    try:
        _fill_span(span, record)
        if record.get("kind") == "hook.failed":
            error = record.get("error")
            token = _error_type(error) if isinstance(error, str) else None
            span.set_attribute(ERROR_TYPE, token or "HookError")
            span.set_status(Status(StatusCode.ERROR))
    finally:
        span.end(end_time=when)


def _fill_span(span, record: dict[str, Any]) -> None:
    user = record.get("user")
    if isinstance(user, str) and user:
        span.set_attribute("user.id", user)
    method = record.get("method")
    if isinstance(method, str) and method in _METHODS:
        span.set_attribute(HTTP_REQUEST_METHOD, method)
    path = record.get("path")
    templated = _template_path(path) if isinstance(path, str) else None
    if templated is not None and len(templated) <= 200:
        span.set_attribute(URL_PATH, templated)
    template = _route_template(record.get("route"))
    if record.get("kind") == "request.proxied" and templated is not None:
        span.set_attribute("http.route", templated)
    elif template is not None:
        span.set_attribute("http.route", template)
    status = record.get("status")
    if isinstance(status, int) and not isinstance(status, bool):
        span.set_attribute(HTTP_RESPONSE_STATUS_CODE, status)
    hook = record.get("hook")
    if isinstance(hook, str) and hook:
        span.set_attribute("fitit.hook", hook)
    tool = record.get("tool")
    if isinstance(tool, str) and tool:
        span.set_attribute("fitit.tool", tool)
    if isinstance(record.get("enabled"), bool):
        span.set_attribute("fitit.enabled", record["enabled"])
    if isinstance(record.get("hooks"), list):
        span.set_attribute("fitit.hooks.count", len(record["hooks"]))
    if isinstance(record.get("logs"), list):
        span.set_attribute("fitit.logs.count", len(record["logs"]))
    ms = record.get("ms")
    if isinstance(ms, (int, float)) and not isinstance(ms, bool) and ms >= 0:
        span.set_attribute("fitit.duration_ms", float(ms))
    kind = str(record.get("kind") or "")
    feature_id = record.get("feature_id")
    if isinstance(feature_id, int) and not isinstance(feature_id, bool) and feature_id >= 0:
        span.set_attribute("fitit.feature.id", feature_id)
    version = record.get("version")
    if isinstance(version, int) and not isinstance(version, bool) and version >= 0:
        if kind.startswith("feature."):
            span.set_attribute("fitit.feature.version", version)
        elif kind == "hook.restored":
            span.set_attribute("fitit.hook.version", version)
    if isinstance(record.get("created"), bool):
        span.set_attribute("fitit.feature.created", record["created"])
    hook = record.get("hook")
    if isinstance(hook, str) and hook.startswith("@") and hook[1:].isdigit():
        span.set_attribute("fitit.feature.id", int(hook[1:]))
    for source, target in (("input_tokens", "fitit.agent.input_tokens"), ("output_tokens", "fitit.agent.output_tokens")):
        count = record.get(source)
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            span.set_attribute(target, count)
    for name in _SENSITIVE:
        if name in record and record[name] is not None:
            span.set_attribute(f"fitit.{name}.sha256", _sha(record[name]))


def _template_path(path: str) -> str:
    if not path.startswith("/"):
        return path
    return re.sub(r"/\d+(?=/|$)", "/{id}", path)


def _route_template(route: Any) -> str | None:
    if not isinstance(route, str) or not route:
        return None
    head, _, tail = route.partition(" ")
    if head in _METHODS and tail.startswith("/"):
        return tail
    if route.startswith("/"):
        return route
    return None


def _error_type(message: str) -> str | None:
    head = message.split(":", 1)[0].strip()
    if head.isidentifier() and len(head) <= 64:
        return head
    return None


def _sha(value: Any) -> str:
    raw = value if isinstance(value, str) else json.dumps(value, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _flush_exporter(timeout_ms: int) -> None:
    global _exporter_failed
    provider = otel_trace.get_tracer_provider()
    force = getattr(provider, "force_flush", None)
    if force is None:
        return
    try:
        if force(timeout_millis=timeout_ms) is False:
            _exporter_failed += 1
            _note("exporter flush timed out")
    except Exception as exc:
        _exporter_failed += 1
        _note(f"force_flush failed: {type(exc).__name__}")


def _discard_ticket(ticket: dict[str, Any]) -> None:
    with _cond:
        _inflight[:] = [existing for existing in _inflight if existing is not ticket]
        _cond.notify_all()


def _drop(reason: str) -> None:
    global _dropped
    _dropped += 1
    _note(reason)


def _set_problems(problems: list[str]) -> None:
    global _journal_problems
    _journal_problems = problems


def _note(message: str) -> None:
    _messages.append(message)
    if message.startswith(("audit queue full", "journal:", "isolated", "sink closed", "another trace writer")):
        print(f"fitit-trace {message}", file=sys.stderr, flush=True)
