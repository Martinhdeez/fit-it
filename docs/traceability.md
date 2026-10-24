# Traceability

Producers call `trace.emit(kind, **fields)`. The sink writes a journal and, when a collector is configured, exports a redacted span. Decision: [ADR 0026](adr/0026-trace-journal-and-otel-export.md).

## Run

```bash
docker compose up --build
```

The core is on port 8000. Jaeger is [http://127.0.0.1:16686](http://127.0.0.1:16686). The core does not wait for Jaeger. `make check` blanks the exporter so the suite does not need the collector.

## Journal

Compose sets `FITIT_TRACE_DIR=/var/lib/fitit-trace` on the `fitit-trace` volume. The file is `events.jsonl`. It is not in Postgres and not in the hook store.

Each line has `ts`, `kind`, the producer fields, and `seq`. `seq` is assigned when the line is committed. It keeps increasing after a restart. `trace_id` and `parent_span_id` are the active parent span. They are absent when the event had no parent, so the row is not a Jaeger link.

Read a page from a new container:

```bash
docker compose run --rm --no-deps -e FITIT_TRACE_DIR=/var/lib/fitit-trace core \
  uv run python -c "import trace; print(trace.read_events(limit=20))"
```

Pass the last `seq` as `after` for the next page. The scan walks the file from the start. A bad line is skipped. Bytes that were not valid UTF-8 are reported. A crash fragment is copied to `events.corrupt` and the next record stays on its own line.

`flush` is true only when every record accepted before that call is on disk. A later `emit` does not extend that wait. Exporter errors increment `diagnostics()["exporter_failed"]` and do not by themselves make `flush` false.

The queue holds 10,000 records. Above that, the new record is counted in `diagnostics()["dropped"]` and is not in the file. REQ-TR1-2 is not claimed for those dropped records. One process holds `writer.lock`. A second process does not append.

Only an operator with the volume should read the journal. It contains prompts, diffs and errors. Jaeger does not, and Jaeger forgets traces when it restarts.

## Spans

FastAPI creates one server span and reads `traceparent`. `emit` adds an internal span for the event. `ms` is `fitit.duration_ms` at the time of the call. `version` is `fitit.hook.version`. Token counts are `fitit.agent.input_tokens` and `fitit.agent.output_tokens`.

`url.query` is replaced with `REDACTED` on every span from this provider. Free-form event fields are stored as SHA-256 only. The original text stays in the journal.

Service name: `fit-it-core`. OTLP/HTTP base URL: `OTEL_EXPORTER_OTLP_ENDPOINT` (`http://jaeger:4318` in Compose).
