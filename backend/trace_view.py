"""Readable timeline of the audit journal. Operator tool, not an HTTP route."""

import argparse
import json

import trace


def summary(row: dict) -> str:
    kind = row.get("kind")
    if kind == "agent.message":
        return str(row.get("text", ""))[:80]
    if kind == "agent.answer":
        return str(row.get("text", ""))[:80]
    if kind in ("hook.saved", "feature.forked"):
        diff = str(row.get("diff", "")).splitlines()
        head = diff[0] if diff else row.get("hook", "")
        return f"{row.get('hook', '')} {head}"[:80]
    if kind == "hook.rejected":
        return f"{row.get('hook', '')} {row.get('reason', '')}"[:80]
    if kind in ("hook.applied", "hook.failed", "request.proxied"):
        return f"{row.get('hook', row.get('path', ''))} {row.get('ms', '')}".strip()[:80]
    if str(kind).startswith("feature."):
        return f"feature {row.get('feature_id')} v{row.get('version', '')}".strip()[:80]
    return str(row.get("hook") or row.get("path") or "")[:80]


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the Fit-it audit timeline")
    parser.add_argument("--user", default=None)
    parser.add_argument("--after", type=int, default=0)
    args = parser.parse_args()
    for row in trace.read_events(after=args.after, limit=1000):
        if args.user is not None and row.get("user") != args.user:
            continue
        print(f"{row.get('seq')} {row.get('user')} {row.get('kind')} {summary(row)}")


if __name__ == "__main__":
    main()
