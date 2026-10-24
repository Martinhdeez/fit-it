"""Trace seam: the core's only interface to observability. Spec: docs/specs/trace-events.md."""
import json
import time


def emit(kind: str, **fields) -> None:
    try:
        print(json.dumps({"ts": time.time(), "kind": kind, **fields}, default=str), flush=True)
    except Exception:
        pass
