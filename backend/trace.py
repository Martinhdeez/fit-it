"""Trace seam. Spec: docs/specs/trace-events.md. Default implementation: one JSON line per event."""
import json
import time


def emit(kind: str, **fields) -> None:
    try:
        print(json.dumps({"ts": time.time(), "kind": kind, **fields}, default=str), flush=True)
    except Exception:
        pass
