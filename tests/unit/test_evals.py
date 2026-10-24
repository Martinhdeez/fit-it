import os
import re

import httpx
import psycopg
import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import agent
import hooks
import trace
import cases
import run_evals

FRIDAY_HOOK = '''ROUTE = "GET /api/invoices"

def run(data, ctx):
    for invoice in data:
        if datetime.fromisoformat(invoice["date"]).weekday() == 4:
            invoice["amount"] = round(invoice["amount"] * 0.85, 2)
    ctx.log("discounted Friday invoices")
    return data
'''
SPEC_IDS = {"friday_discount", "mask_phone_debtors", "debt_per_doctor", "sort_by_amount", "unpaid_only", "hide_dni",
            "age_column", "appointments_sorted", "vat_column", "uppercase_names", "edit_existing", "undo_change",
            "refuse_unsafe", "subscribe_hide_dni", "search_then_write", "publish_feature", "fork_feature"}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("FITIT_AGENT_MESSAGES_PER_HOUR", "100000")
    monkeypatch.setenv("BASE_URL", os.environ.get("DEMO_URL", "http://demo:8001"))


def case(id):
    return next(c for c in cases.CASES if c["id"] == id)


def eval_schemas():
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        return conn.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'eval\\_%'").fetchall()


def run(fn, id="friday_discount"):
    url, emit = os.environ["DATABASE_URL"], trace.emit
    with agent.agent.override(model=FunctionModel(fn)):
        result = run_evals.run_case(case(id) if isinstance(id, str) else id)
    assert os.environ["DATABASE_URL"] == url and trace.emit is emit and not eval_schemas()
    return result


def hook_then_text(messages, info):
    if len(messages) == 1:
        return ModelResponse(parts=[ToolCallPart("write_hook", {"name": "friday", "code": FRIDAY_HOOK})])
    trace.emit("agent.answer", user="gerente", text="", input_tokens=1000, output_tokens=7)
    return ModelResponse(parts=[TextPart("done")])


def test_cases_shape():
    ids = [c["id"] for c in run_evals.CASES]
    assert len(ids) == 17 and len(set(ids)) == len(ids) and SPEC_IDS <= set(ids)
    assert [c["id"] for c in run_evals.CASES if "setup" in c] == ["subscribe_hide_dni", "search_then_write",
                                                                  "fork_feature"]
    for c in run_evals.CASES:
        assert c["user"] in {"recepcion", "dr_garcia", "gerente"} and c["turns"] and callable(c["check"])
        assert c["route"].startswith(("/api/", "/x/"))


def test_checks_reject_unchanged_data():
    for c in cases.CASES:
        if c["route"].startswith("/x/"):
            continue
        demo = httpx.Client(base_url=os.environ["BASE_URL"])
        demo.get("/login", params={"user": c["user"]})
        get = lambda path: demo.get(path).json() if not path.startswith("/fitit/") else []
        before = get(c["route"])
        if c["id"] == "refuse_unsafe":
            c["check"](before, before, get=get)
        else:
            with pytest.raises(AssertionError):
                c["check"](before, before, get=get)


def test_correct_hook_passes_and_tokens_are_summed():
    r = run(hook_then_text)
    assert r["passed"], r["detail"]
    assert r["id"] == "friday_discount" and r["seconds"] >= 0
    assert r["input_tokens"] >= 1000 and r["output_tokens"] >= 7


def test_text_only_fails_with_detail():
    r = run(lambda messages, info: ModelResponse(parts=[TextPart("sure")]))
    assert not r["passed"] and r["detail"].startswith("check:")


def test_raising_model_fails_without_raising():
    def boom(messages, info):
        raise RuntimeError("model down")
    r = run(boom)
    assert not r["passed"] and "model down" in r["detail"]
    assert r["input_tokens"] is None and r["output_tokens"] is None


def test_main_runs_selected_cases(monkeypatch, capsys):
    monkeypatch.setenv("EVAL_CASES", "friday_discount, refuse_unsafe")
    with agent.agent.override(model=FunctionModel(hook_then_text)):
        code = run_evals.main()
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("PASS friday_discount")
    assert lines[1].startswith("FAIL refuse_unsafe")  # the scripted model writes the discount for every case
    assert lines[2].startswith("pass rate: 1/2") and code == 1


def test_main_rejects_unknown_ids(monkeypatch, capsys):
    monkeypatch.setenv("EVAL_CASES", "nope")
    assert run_evals.main() == 1 and "nope" in capsys.readouterr().out


def test_setup_runs_in_the_case_schema_before_the_request():
    def setup():
        hooks.save_hook("gerente", "friday", FRIDAY_HOOK)
    seen = []
    c = {**case("friday_discount"), "setup": setup, "check": lambda before, after, get: seen.append(before)}
    r = run(lambda messages, info: ModelResponse(parts=[TextPart("ok")]), c)
    assert r["passed"], r["detail"]
    fridays = [i for i in seen[0] if cases.is_friday(i["date"])]
    assert fridays
    demo = httpx.Client(base_url=os.environ["BASE_URL"])
    demo.get("/login", params={"user": "gerente"})
    raw = {i["id"]: i["amount"] for i in demo.get("/api/invoices").json()}
    assert all(cases.near(i["amount"], round(raw[i["id"]] * 0.85, 2)) for i in fridays)


def test_raising_setup_fails_the_case_and_drops_the_schema():
    def setup():
        raise RuntimeError("seed broke")
    r = run(lambda messages, info: ModelResponse(parts=[TextPart("ok")]), {**case("friday_discount"), "setup": setup})
    assert not r["passed"] and r["detail"].startswith("setup:") and "seed broke" in r["detail"]


def search_then_subscribe(messages, info):
    last = messages[-1].parts[-1]
    if getattr(last, "tool_name", None) or last.part_kind != "user-prompt":
        return ModelResponse(parts=[TextPart("done")])
    if "subscribe" not in last.content:
        return ModelResponse(parts=[ToolCallPart("search_features", {"query": "DNI"})])
    found = re.search(r"@(\d+)", str(messages))
    return ModelResponse(parts=[ToolCallPart("subscribe_feature", {"feature_id": int(found.group(1))})])


def test_subscribe_hide_dni_passes_with_search_then_subscribe():
    r = run(search_then_subscribe, "subscribe_hide_dni")
    assert r["passed"], r["detail"]


def test_subscribe_hide_dni_fails_with_text_only():
    r = run(lambda messages, info: ModelResponse(parts=[TextPart("sure")]), "subscribe_hide_dni")
    assert not r["passed"] and r["detail"].startswith("check:")
