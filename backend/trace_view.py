"""Readable timeline of the audit journal. Operator tool, not an HTTP route."""

import argparse
import json

import trace


def summary(row: dict) -> str:
    kind = row.get("kind")
    if kind == "agent.message":
        body = str(row.get("text", ""))
    elif kind == "agent.answer":
        body = str(row.get("text", ""))
    elif kind in ("hook.saved", "feature.forked"):
        diff = str(row.get("diff", "")).splitlines()
        head = diff[0] if diff else row.get("hook", "")
        body = f"{row.get('hook', '')} {head}"
    elif kind == "hook.rejected":
        body = f"{row.get('hook', '')} {row.get('reason', '')}"
    elif kind in ("hook.applied", "hook.failed", "request.proxied"):
        body = f"{row.get('hook', row.get('path', ''))} {row.get('ms', '')}".strip()
    elif str(kind).startswith("feature."):
        body = f"feature {row.get('feature_id')} v{row.get('version', '')}".strip()
    else:
        body = str(row.get("hook") or row.get("path") or "")
    conversation = row.get("conversation")
    if (
        str(kind).startswith("agent.")
        and isinstance(conversation, str)
        and conversation
        and conversation != "default"
    ):
        body = f"[{conversation}] {body}"
    return body[:80]


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the Fit-it audit timeline")
    parser.add_argument("--user", default=None)
    parser.add_argument("--conversation", default=None)
    parser.add_argument("--after", type=int, default=0)
    args = parser.parse_args()
    after = args.after
    while True:
        page = trace.read_events(after=after, limit=1000)
        if not page:
            break
        for row in page:
            if args.user is not None and row.get("user") != args.user:
                continue
            if args.conversation is not None and row.get("conversation") != args.conversation:
                continue
            print(f"{row.get('seq')} {row.get('user')} {row.get('kind')} {summary(row)}")
        after = page[-1]["seq"]


if __name__ == "__main__":
    main()
