import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, format_span_id, format_trace_id, get_tracer

import trace

MEMORY = InMemorySpanExporter()
BACKEND = Path(__file__).resolve().parents[2] / "backend"


@pytest.fixture
def audit(tmp_path, monkeypatch):
    monkeypatch.setenv("FITIT_TRACE_DIR", str(tmp_path))
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    trace.startup()
    trace.configure(exporter=MEMORY)
    yield tmp_path
    trace._hold.set()
    trace.flush(timeout_ms=3000)


def _rows(**match):
    rows = trace.read_events(after=0, limit=100000)
    for key, value in match.items():
        rows = [row for row in rows if row.get(key) == value]
    return rows


def test_emit_accepts_every_kind_and_anonymous_user(audit):
    kinds = [
        ("request.proxied", {"user": None, "method": "GET", "path": "/api/invoices", "status": 200, "ms": 1.5, "hooks": []}),
        ("hook.applied", {"user": "gerente", "hook": "friday", "route": "GET /api/invoices", "ms": 2, "logs": ["ok"]}),
        ("hook.failed", {"user": "gerente", "hook": "friday", "route": "GET /api/invoices", "error": "KeyError: missing"}),
        ("agent.message", {"user": None, "text": "hello"}),
        ("agent.tool", {"user": "gerente", "tool": "list_hooks", "input": {"path": "/"}}),
        ("hook.saved", {"user": "gerente", "hook": "friday", "route": "GET /api/invoices", "diff": "+amount"}),
        ("hook.rejected", {"user": "gerente", "hook": "friday", "reason": "import os"}),
        ("hook.toggled", {"user": "gerente", "hook": "friday", "enabled": False}),
        ("hook.deleted", {"user": "gerente", "hook": "friday"}),
        ("hook.restored", {"user": "gerente", "hook": "friday", "version": 2}),
        ("agent.answer", {"user": "gerente", "text": "done", "input_tokens": 3, "output_tokens": 4}),
    ]
    for kind, fields in kinds:
        assert trace.emit(kind, **fields) is None
    assert trace.flush()
    got = trace.read_events(limit=20)
    assert [row["kind"] for row in got] == [kind for kind, _ in kinds]
    assert got[0]["user"] is None
    assert got[-1]["input_tokens"] == 3


def test_nested_values_are_detached(audit):
    logs = ["keep"]
    cycle = []
    cycle.append(cycle)

    class Boom:
        def __str__(self):
            raise RuntimeError("nope")

        def __repr__(self):
            raise RuntimeError("nope")

    trace.emit("hook.applied", user="u", hook="h", route="GET /x", ms=1, logs=logs)
    logs.append("later")
    trace.emit("agent.message", user=None, text=cycle, diff=Boom())
    assert trace.flush()
    assert _rows(kind="hook.applied")[-1]["logs"] == ["keep"]
    assert _rows(kind="agent.message")[-1]["text"] == ["<cycle>"]
    assert _rows(kind="agent.message")[-1]["diff"] == "<unrepresentable>"


def test_cursor_survives_process_restart(tmp_path, monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    code = (
        "import os, sys\n"
        "os.environ['FITIT_TRACE_DIR'] = sys.argv[1]\n"
        "import trace\n"
        "trace.startup()\n"
        "trace.emit('agent.message', user='a', text=sys.argv[2])\n"
        "assert trace.flush()\n"
        "print(trace.read_events()[-1]['seq'])\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND)
    env.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
    env.pop("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", None)

    def once(text):
        proc = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path), text],
            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        assert proc.returncode == 0, proc.stderr + proc.stdout
        return int(proc.stdout.strip().splitlines()[-1])

    first = once("one")
    (tmp_path / "events.jsonl").write_bytes((tmp_path / "events.jsonl").read_bytes() + b'{"ts":')
    second = once("two")
    assert second > first
    monkeypatch.setenv("FITIT_TRACE_DIR", str(tmp_path))
    rows = trace.read_events(after=first, limit=10)
    assert [row["text"] for row in rows] == ["two"]
    assert b'{"ts":' in (tmp_path / "events.corrupt").read_bytes()


def test_concurrent_admission_preserves_pagination(audit):
    trace._hold.clear()
    trace._in_hold.clear()
    names = ["first", "second", "third"]

    def send(name):
        trace.emit("agent.message", user=name, text=name)

    threads = [threading.Thread(target=send, args=(name,)) for name in names]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert trace._in_hold.wait(2)
    trace._hold.set()
    assert trace.flush()
    page = []
    after = 0
    while True:
        chunk = trace.read_events(after=after, limit=1)
        if not chunk:
            break
        page.append(chunk[0])
        after = chunk[0]["seq"]
    assert sorted(row["text"] for row in page) == names
    assert [row["seq"] for row in page] == sorted(row["seq"] for row in page)
    assert len({row["seq"] for row in page}) == 3


def test_append_after_incomplete_tail_keeps_the_next_record(audit):
    path = audit / "events.jsonl"
    path.write_text('{"ts": 1, "kind": "agent.answer", "user": "old", "text": "kept", "seq": 1}\n{"ts":', encoding="utf-8")
    trace._bound.discard(str(audit.resolve()))
    trace._next_seq.pop(str(audit.resolve()), None)
    trace.emit("agent.answer", user="new", text="visible")
    assert trace.flush()
    rows = trace.read_events(limit=10)
    assert [row["text"] for row in rows] == ["kept", "visible"]
    assert (audit / "events.corrupt").read_text(encoding="utf-8").endswith('{"ts":')


def test_complete_json_without_newline_stays_readable(audit):
    record = {"ts": 1, "kind": "agent.answer", "user": "old", "text": "whole", "seq": 4}
    (audit / "events.jsonl").write_text(json.dumps(record), encoding="utf-8")
    trace._bound.discard(str(audit.resolve()))
    trace._next_seq.pop(str(audit.resolve()), None)
    trace.emit("agent.answer", user="new", text="next")
    assert trace.flush()
    assert [row["text"] for row in trace.read_events(limit=10)] == ["whole", "next"]


def test_invalid_utf8_does_not_raise(audit):
    (audit / "events.jsonl").write_bytes(b"\xff\xfe not utf8\n")
    assert trace.read_events() == []
    assert "invalid utf-8" in trace.diagnostics()["journal"]


def test_journal_failure_fails_the_flush(audit, monkeypatch):
    def broken(_path, _lines):
        raise OSError("disk")

    monkeypatch.setattr(trace, "_append", broken)
    assert trace.emit("agent.answer", user="disk-user", text="lost") is None
    assert trace.flush() is False
    assert _rows(user="disk-user") == []
    assert trace.diagnostics()["failed"] >= 1


def test_emit_stays_fast_when_the_writer_is_blocked(audit):
    trace._hold.clear()
    trace._in_hold.clear()
    trace.emit("agent.message", user="hold", text="block")
    assert trace._in_hold.wait(2)
    started = time.perf_counter()
    for n in range(1000):
        trace.emit("request.proxied", user="speed", method="GET", path="/api/invoices", status=200, ms=1, hooks=[n])
    mean_ms = (time.perf_counter() - started) * 1000 / 1000
    print(f"REQ-TR1-3 mean_ms={mean_ms:.4f}")
    assert mean_ms < 5
    trace._hold.set()
    assert trace.flush(timeout_ms=20000)
    saved = [row["hooks"] for row in _rows(user="speed")]
    assert saved[0] == [0] and saved[-1] == [999] and len(saved) == 1000


def test_queue_overflow_is_counted(audit):
    trace._hold.clear()
    trace._in_hold.clear()
    trace.emit("agent.message", user="hold", text="block")
    assert trace._in_hold.wait(2)
    previous = trace._queue.maxsize
    trace._queue.maxsize = 1
    try:
        before = trace.diagnostics()["dropped"]
        trace.emit("agent.message", user="overflow", text="queued")
        trace.emit("agent.message", user="overflow", text="DROPME")
        assert trace.diagnostics()["dropped"] == before + 1
    finally:
        trace._queue.maxsize = previous
        trace._hold.set()
    assert trace.flush()
    assert [row["text"] for row in _rows(user="overflow")] == ["queued"]


def test_span_hides_content_and_maps_iteration_fields(audit):
    MEMORY.clear()
    marker = "MARKER-secret-prompt"
    trace.emit(
        "agent.answer",
        user="gerente",
        text=marker,
        input_tokens=8,
        output_tokens=9,
        hook="friday",
        route="GET /api/invoices",
        ms=5,
    )
    trace.emit("hook.restored", user="gerente", hook="friday", version=3)
    assert trace.flush()
    answer = [span for span in MEMORY.get_finished_spans() if span.name == "agent.answer"][-1]
    blob = json.dumps(dict(answer.attributes or {}), default=str)
    assert marker not in blob
    assert answer.attributes["fitit.text.sha256"]
    assert answer.attributes["fitit.agent.input_tokens"] == 8
    assert answer.attributes["http.route"] == "/api/invoices"
    assert answer.attributes["fitit.duration_ms"] == 5
    assert answer.start_time == answer.end_time
    restored = [span for span in MEMORY.get_finished_spans() if span.name == "hook.restored"][-1]
    assert restored.attributes["fitit.hook.version"] == 3


def test_native_query_string_is_redacted(audit):
    MEMORY.clear()
    from fastapi.testclient import TestClient
    import main

    marker = "REVIEW_QUERY_SECRET_452"
    with TestClient(main.app) as client:
        response = client.get(f"/fitit/embed.js?token={marker}")
    assert response.status_code == 200
    spans = MEMORY.get_finished_spans()
    servers = [span for span in spans if span.kind == SpanKind.SERVER]
    assert len(servers) == 1
    for span in spans:
        values = json.dumps(dict(span.attributes or {}), default=str)
        assert marker not in values
        assert (span.status.description or "") == "" or marker not in span.status.description


def test_incoming_traceparent_is_the_server_parent(audit):
    MEMORY.clear()
    from fastapi.testclient import TestClient
    import main

    header = "00-11111111111111111111111111111111-2222222222222222-01"
    with TestClient(main.app) as client:
        client.get("/fitit/embed.js", headers={"traceparent": header})
    server = [span for span in MEMORY.get_finished_spans() if span.kind == SpanKind.SERVER][-1]
    assert format_trace_id(server.parent.trace_id) == "11111111111111111111111111111111"
    assert format_span_id(server.parent.span_id) == "2222222222222222"


def test_context_free_event_has_no_fake_trace_id(audit):
    trace.emit("agent.message", user=None, text="alone")
    assert trace.flush()
    row = _rows(kind="agent.message")[-1]
    assert "trace_id" not in row


def test_child_span_keeps_the_parent_trace(audit):
    MEMORY.clear()
    tracer = get_tracer("fit-it.test")
    with tracer.start_as_current_span("request"):
        trace.emit("hook.applied", user="u", hook="h", route="GET /api/invoices", ms=1, logs=[])
    assert trace.flush()
    child = [span for span in MEMORY.get_finished_spans() if span.name == "hook.applied"][-1]
    parent = [span for span in MEMORY.get_finished_spans() if span.name == "request"][-1]
    assert child.parent is not None
    assert child.parent.span_id == parent.context.span_id
    row = _rows(kind="hook.applied")[-1]
    assert row["parent_span_id"] == format_span_id(parent.context.span_id)
