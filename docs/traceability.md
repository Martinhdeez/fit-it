# Traceability

Producers call `trace.emit(kind, **fields)`. The sink writes a journal and, when a collector is configured, exports a redacted span. Decision: [ADR 0026](adr/0026-trace-journal-and-otel-export.md).

## Run

```bash
docker compose up --build
```

`make check` sets `FITIT_TRACE_DIR=/tmp/fitit-trace` and blanks the exporter, so the suite does not need Jaeger and does not write the operator journal. Evals replace `trace.emit` and do not write the journal either.

## Journal

Compose sets `FITIT_TRACE_DIR=/var/lib/fitit-trace` on the `fitit-trace` volume. The file is `events.jsonl`. It is not in Postgres and not in the hook store.

Each line has `ts`, `kind`, the producer fields, and `seq`. `seq` is assigned when the line is committed. A bad UTF-8 byte does not restart it. `trace_id` and `parent_span_id` are the active parent span. They are absent when the event had no parent. One chat connection is one trace until the agent opens a span per turn.

Docker logs show `seq kind user` only. Prompts and diffs stay in the journal. A crash fragment is copied to `events.corrupt` and cut off with `truncate`, so the earlier bytes stay put. There is no rotation.

Read a page as JSON lines:

```bash
docker compose run --rm --no-deps core uv run python -c "import json, trace; [print(json.dumps(r)) for r in trace.read_events(limit=20)]"
```

The demo timeline, with the prompt and the diff, is:

```bash
docker compose run --rm --no-deps core uv run python trace_view.py --user gerente
```

Pass the last `seq` as `after` for the next page. The scan walks the file from the start. A bad line is skipped. Bytes that were not valid UTF-8 are reported. A crash fragment is copied to `events.corrupt` and the next record stays on its own line.

`flush` is true only when every record accepted before that call is on disk. A later `emit` does not extend that wait. Exporter errors increment `diagnostics()["exporter_failed"]` and do not by themselves make `flush` false.

The queue holds 10,000 records. Above that, the new record is counted in `diagnostics()["dropped"]` and is not in the file. REQ-TR1-2 is not claimed for those dropped records. One process holds `writer.lock`. A second process does not append.

Only an operator with the volume should read the journal. It contains prompts, diffs and errors. Jaeger does not, and Jaeger forgets traces when it restarts.

## Spans

FastAPI creates one server span and reads `traceparent`. `emit` adds an internal span for the event. `ms` is `fitit.duration_ms` at the time of the call. Numeric path segments are `{id}`. `url.query` is `REDACTED`. Marketplace ids are `fitit.feature.id`, `fitit.feature.version` and `fitit.feature.created`. Hook restore keeps `fitit.hook.version`.

Service name: `fit-it-core`. OTLP/HTTP base URL: `OTEL_EXPORTER_OTLP_ENDPOINT` (`http://jaeger:4318` in Compose). Jaeger is `jaegertracing/jaeger:2.11.0`, UI at [http://127.0.0.1:16686](http://127.0.0.1:16686). Its traces are memory only.
