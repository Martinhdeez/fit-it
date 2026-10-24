"""Real-model eval harness. Spec: docs/specs/evals.md. Run with `make eval`; never part of `make check`."""
import os
import sys
import time
import uuid

import psycopg
from fastapi.testclient import TestClient

import db
import main as core
import trace
from cases import CASES

DEFAULT_URL = "postgresql://fitit:fitit@postgres:5432/fitit"  # same default as db.connect


def get_json(client, path):
    r = client.get(path)
    assert r.status_code == 200, f"GET {path} returned {r.status_code}: {r.text[:300]}"
    return r.json()


def token_sum(events, key):
    values = [f[key] for kind, f in events if kind == "agent.answer" and f.get(key) is not None]
    return sum(values) if values else None


def run_case(case) -> dict:
    """Runs one case in its own eval_<hex> schema. Never raises."""
    start = time.monotonic()
    saved_url, saved_emit = os.environ.get("DATABASE_URL"), trace.emit
    base = (saved_url or DEFAULT_URL).split("?")[0]
    schema = f"eval_{uuid.uuid4().hex}"
    events, errors, step = [], [], "setup"
    try:
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(f"CREATE SCHEMA {schema}")
        os.environ["DATABASE_URL"] = f"{base}?options=-csearch_path%3D{schema}"
        db.init()
        if "setup" in case:
            case["setup"]()
        trace.emit = lambda kind, **fields: events.append((kind, fields))

        step = "login"
        client = TestClient(core.app, follow_redirects=False)
        client.get("/login", params={"user": case["user"]})
        route = case["route"]

        step = "before"
        before = None if route.startswith("/x/") else get_json(client, route)

        step = "turns"
        with client.websocket_connect("/fitit/ws") as ws:
            for text in case["turns"]:
                ws.send_json({"type": "message", "text": text})
                while (event := ws.receive_json())["type"] != "done":
                    if event["type"] == "error":
                        errors.append(event.get("message", ""))

        step = "after"
        after = get_json(client, route)

        step = "check"
        case["check"](before, after, get=lambda path: get_json(client, path))
        passed, detail = True, ""
    except Exception as e:
        passed, detail = False, f"{step}: {str(e) or type(e).__name__}"
        if errors:
            detail += f" (last error: {errors[-1]})"
    finally:
        trace.emit = saved_emit
        if saved_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_url
        try:
            with psycopg.connect(base, autocommit=True) as conn:
                conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        except Exception as e:
            passed, detail = False, detail or f"cleanup: {e}"
    return {"id": case["id"], "passed": passed, "detail": detail, "seconds": round(time.monotonic() - start, 1),
            "input_tokens": token_sum(events, "input_tokens"), "output_tokens": token_sum(events, "output_tokens")}


def fmt(n):
    return "n/a" if n is None else str(n)


def main(argv=None) -> int:
    ids = [i.strip() for i in os.environ.get("EVAL_CASES", "").split(",") if i.strip()]
    unknown = set(ids) - {c["id"] for c in CASES}
    if unknown:
        print(f"unknown case ids: {', '.join(sorted(unknown))}")
        return 1
    selected = [c for c in CASES if not ids or c["id"] in ids]
    width = max((len(c["id"]) for c in selected), default=0)
    results = []
    for case in selected:
        r = run_case(case)
        results.append(r)
        line = (f"{'PASS' if r['passed'] else 'FAIL'} {r['id']:<{width}}  {r['seconds']:>5.1f}s"
                f"  in={fmt(r['input_tokens'])} out={fmt(r['output_tokens'])}")
        print(line + (f"  {r['detail']}" if r["detail"] else ""), flush=True)
    passed = sum(r["passed"] for r in results)
    totals = [token_sum([("agent.answer", {k: r[k]}) for r in results], k) for k in ("input_tokens", "output_tokens")]
    print(f"pass rate: {passed}/{len(results)} ({round(100 * passed / max(len(results), 1))}%)"
          f"  tokens: in={fmt(totals[0])} out={fmt(totals[1])}")
    return 0 if results and passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
