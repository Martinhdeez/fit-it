from main import CodeBox, check_code, run_pipeline, user_boxes


def rejects(code):
    try:
        check_code(code)
    except ValueError:
        return True
    return False


assert rejects("import os")
assert rejects("data.__class__")
assert rejects("open('/etc/passwd')")
assert rejects("__import__('os')")
assert not rejects("data = [r for r in data if r['debt'] > 0]")

mask = CodeBox(id="m", name="Mask phones", code="""
for r in data:
    if r['debt'] > 0:
        r['phone'] = '***'
        context['badges'][r['id']] = ['debt']
print('masked', sum(r['phone'] == '***' for r in data))
""")
user_boxes["recepcion"] = [mask]
out = run_pipeline("recepcion")
assert out["blocked_by"] is None
assert all(r["phone"] == "***" for r in out["data"] if r["debt"] > 0)
assert out["logs"] == [{"box": "Mask phones", "msg": "masked 5"}]

scope = CodeBox(id="s", name="Own patients", code="data = [r for r in data if r['doctor_id'] == context['user']['doctor_id']]")
user_boxes["dr_garcia"] = [scope]
assert {r["doctor_id"] for r in run_pipeline("dr_garcia")["data"]} == {1}

broken = CodeBox(id="b", name="Broken", code="data = data[0]['missing']")
user_boxes["gerente"] = [broken]
out = run_pipeline("gerente")
assert out["data"] == [] and out["blocked_by"] == "b"

broken.enabled = False
assert len(run_pipeline("gerente")["data"]) == 8

print("ok")
