"""Acceptance tests for EV2 (marketplace eval cases and case `setup`), written from docs/specs/evals.md,
docs/specs/agent.md, docs/specs/marketplace.md and the 03-marketplace plan only.

No test calls a real model: every agent turn runs under `agent.agent.override(model=FunctionModel(...))`.

`run_evals`, `psycopg` and `marketplace` are imported lazily, so this file collects before EV2 lands.
As in test_ev1.py, the harness creates and drops its own `eval_<32 hex>` schema from the base
`DATABASE_URL`, so these tests do not use the plan's `database` fixture.
"""

import importlib
import os
import re
import uuid

import httpx
import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import agent as agent_module

DEMO_URL = os.environ.get("DEMO_URL", "http://demo:8001")

MARKETPLACE_IDS = {"subscribe_hide_dni", "search_then_write", "publish_feature", "fork_feature"}
SETUP_IDS = MARKETPLACE_IDS - {"publish_feature"}
SPEC_IDS = {
    "friday_discount",
    "mask_phone_debtors",
    "debt_per_doctor",
    "sort_by_amount",
    "unpaid_only",
    "hide_dni",
    "age_column",
    "appointments_sorted",
    "vat_column",
    "uppercase_names",
    "edit_existing",
    "undo_change",
    "refuse_unsafe",
} | MARKETPLACE_IDS

MARKER_CODE = '''ROUTE = "GET /api/invoices"

def run(data, ctx):
    for invoice in data:
        invoice["ev2_marker"] = 1
    return data
'''

SCHEMA_IN_URL = re.compile(r"search_path%3D(eval_[0-9a-f]{32})$")


@pytest.fixture
def run_evals(monkeypatch):
    monkeypatch.setenv("BASE_URL", DEMO_URL)
    monkeypatch.setenv("FITIT_AGENT_MESSAGES_PER_HOUR", "100000")
    monkeypatch.delenv("EVAL_CASES", raising=False)
    return importlib.import_module("run_evals")


def case_by_id(run_evals, case_id):
    return next(c for c in run_evals.CASES if c["id"] == case_id)


def eval_schemas():
    import psycopg

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        rows = conn.execute(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'eval\\_%'"
        ).fetchall()
    return {r[0] for r in rows}


def last_part(messages):
    return messages[-1].parts[-1]


def last_user_text(messages):
    for message in reversed(messages):
        for part in message.parts:
            if part.part_kind == "user-prompt":
                return str(part.content)
    return ""


def text_only(messages, info):
    return ModelResponse(parts=[TextPart("I cannot do that.")])


def call(tool, args):
    return ModelResponse(parts=[ToolCallPart(tool_name=tool, args=args, tool_call_id=f"call_{uuid.uuid4().hex}")])


def search_then_subscribe():
    """Turn 1: search_features, then answer with what it found. Turn 2: subscribe_feature to the id found."""
    found = {}

    def model(messages, info):
        part = last_part(messages)
        if part.part_kind == "tool-return":
            if part.tool_name == "search_features":
                match = re.search(r"@(\d+)", str(part.content))
                if match:
                    found["id"] = int(match.group(1))
                return ModelResponse(parts=[TextPart(f"Found: {part.content}. Subscribe?")])
            return ModelResponse(parts=[TextPart("Done.")])
        if part.part_kind == "retry-prompt":
            return ModelResponse(parts=[TextPart("Something went wrong.")])
        if "subscribe" in last_user_text(messages).lower() and "id" in found:
            return call("subscribe_feature", {"feature_id": found["id"]})
        return call("search_features", {"query": "DNI"})

    return model


def run_with(run_evals, model, case):
    with agent_module.agent.override(model=FunctionModel(model)):
        return run_evals.run_case(case)


# REQ-EV2-1


def test_req_ev2_1_cases_17_unique_ids_with_every_spec_id(run_evals):
    ids = [c["id"] for c in run_evals.CASES]
    assert len(ids) == 17
    assert len(ids) == len(set(ids))
    assert SPEC_IDS <= set(ids)


def test_req_ev2_1_marketplace_cases_except_publish_have_setup(run_evals):
    for case_id in SETUP_IDS:
        assert callable(case_by_id(run_evals, case_id).get("setup")), case_id


def test_req_ev2_1_appointments_sorted_asks_latest_first(run_evals):
    c = case_by_id(run_evals, "appointments_sorted")
    assert any("latest first" in turn for turn in c["turns"]), c["turns"]


def test_req_ev2_1_appointments_sorted_fails_on_the_demo_order(run_evals):
    r = httpx.get(f"{DEMO_URL}/api/appointments", cookies={"session": "gerente"}, timeout=10)
    assert r.status_code == 200
    body = r.json()
    check = case_by_id(run_evals, "appointments_sorted")["check"]
    with pytest.raises(AssertionError):
        check(body, body)


# REQ-EV2-2


def test_req_ev2_2_setup_hook_applies_to_before_and_schema_dropped(run_evals):
    import hooks

    schemas_before = eval_schemas()
    seen = {}

    def setup():
        seen["url"] = os.environ["DATABASE_URL"]
        hooks.save_hook("gerente", "ev2_marker", MARKER_CODE)

    def check(before, after, **_):  # the spec names (before, after); tolerate extra keyword helpers
        seen["before"] = before
        assert before and all(inv.get("ev2_marker") == 1 for inv in before), before

    case = {
        "id": "ev2_setup_hook",
        "user": "gerente",
        "turns": ["Hello"],
        "route": "/api/invoices",
        "check": check,
        "setup": setup,
    }
    result = run_with(run_evals, text_only, case)

    assert result["passed"] is True, result["detail"]
    assert all(inv["ev2_marker"] == 1 for inv in seen["before"])
    match = SCHEMA_IN_URL.search(seen["url"])
    assert match, seen["url"]
    assert match.group(1) not in eval_schemas()
    assert eval_schemas() <= schemas_before


# REQ-EV2-3


def test_req_ev2_3_raising_setup_fails_with_setup_detail(run_evals):
    schemas_before = eval_schemas()
    seen = {}

    def setup():
        seen["url"] = os.environ["DATABASE_URL"]
        raise RuntimeError("seed failed")

    def check(before, after, **_):  # the spec names (before, after); tolerate extra keyword helpers
        pass

    case = {
        "id": "ev2_setup_raises",
        "user": "gerente",
        "turns": ["Hello"],
        "route": "/api/invoices",
        "check": check,
        "setup": setup,
    }
    result = run_with(run_evals, text_only, case)

    assert result["passed"] is False
    assert result["detail"].startswith("setup:"), result["detail"]
    match = SCHEMA_IN_URL.search(seen["url"])
    assert match, seen["url"]
    assert match.group(1) not in eval_schemas()
    assert eval_schemas() <= schemas_before


# REQ-EV2-4


def test_req_ev2_4_search_then_subscribe_passes_subscribe_hide_dni(run_evals):
    schemas_before = eval_schemas()
    result = run_with(run_evals, search_then_subscribe(), case_by_id(run_evals, "subscribe_hide_dni"))
    assert result["id"] == "subscribe_hide_dni"
    assert result["passed"] is True, result["detail"]
    assert eval_schemas() <= schemas_before


def test_req_ev2_4_text_only_fails_subscribe_hide_dni(run_evals):
    result = run_with(run_evals, text_only, case_by_id(run_evals, "subscribe_hide_dni"))
    assert result["passed"] is False
