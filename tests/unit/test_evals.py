import os

import httpx
import psycopg
import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import agent
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
            "refuse_unsafe"}


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
        result = run_evals.run_case(case(id))
    assert os.environ["DATABASE_URL"] == url and trace.emit is emit and not eval_schemas()
    return result


def hook_then_text(messages, info):
    if len(messages) == 1:
        return ModelResponse(parts=[ToolCallPart("write_hook", {"name": "friday", "code": FRIDAY_HOOK})])
    trace.emit("agent.answer", user="gerente", text="", input_tokens=1000, output_tokens=7)
    return ModelResponse(parts=[TextPart("done")])


def test_cases_shape():
    ids = [c["id"] for c in run_evals.CASES]
    assert 10 <= len(ids) <= 20 and len(set(ids)) == len(ids) and SPEC_IDS <= set(ids)
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
        if c["id"] in ("refuse_unsafe", "appointments_sorted"):  # the demo already sorts appointments by start
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
