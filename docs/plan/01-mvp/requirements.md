# 01 — Requirements and verification

Acceptance is decided by tests whose author has not seen the implementation. Each requirement is one testable sentence and names the surface it is checked through.

## Requirements

### OP1 — Test gate and Compose stack

| Id | Requirement | Checked through |
|---|---|---|
| REQ-OP1-1 | `make check` runs every test under `tests/` inside Docker and exits non-zero when any test fails. | Shell |
| REQ-OP1-2 | `docker compose up` serves the core on `localhost:8000`, and the demo app has no port published on the host. | Shell, HTTP |
| REQ-OP1-3 | `make check` passes with `ANTHROPIC_API_KEY` unset. | Shell |

### DA1 — Demo clinic app

| Id | Requirement | Checked through |
|---|---|---|
| REQ-DA1-1 | `GET /api/me` returns `401` without a session, and `{"id": "gerente", ...}` after `GET /login?user=gerente`. | Demo HTTP |
| REQ-DA1-2 | `GET /login?user=nobody` returns `400` and sets no cookie. | Demo HTTP |
| REQ-DA1-3 | Every `/api/*` route other than `/api/me` returns `401` without a session. | Demo HTTP |
| REQ-DA1-4 | `GET /api/patients` as `dr_garcia` returns only patients with `doctor_id` 1, and as `gerente` returns at least 8 patients. | Demo HTTP |
| REQ-DA1-5 | `GET /api/invoices` returns objects with the fields of the spec, dates as `YYYY-MM-DD`, and at least two dates on a Friday. | Demo HTTP |
| REQ-DA1-6 | `GET /openapi.json` lists every `/api/*` route of the spec, each with a non-empty summary. | Demo HTTP |

### HR1 — Hook runtime

| Id | Requirement | Checked through |
|---|---|---|
| REQ-HR1-1 | `check_code` raises `ValueError` for code containing `import`, `from … import`, `global`, `nonlocal`, a name starting with `_`, an attribute starting with `_`, or any name on the spec's deny-list. | `hooks.check_code` |
| REQ-HR1-2 | `check_code` raises `ValueError` when `ROUTE` is missing, not a string, has an unknown method, a path not starting with `/`, or a non-`GET` virtual route, or when `run` is missing. | `hooks.check_code` |
| REQ-HR1-3 | `check_code` accepts the example hook of the spec and returns `("GET", "/api/invoices")`. | `hooks.check_code` |
| REQ-HR1-4 | `save_hook` writes `<WORKSPACES>/<user>/<name>.py` and returns a unified diff; with an invalid name or invalid code it raises `ValueError` and writes nothing. | `hooks.save_hook`, file system |
| REQ-HR1-5 | `run_hooks` runs only the user's enabled hooks whose method and path match, with `{param}` matching exactly one segment, in name order, each receiving the previous output, and returns the applied names. | `hooks.run_hooks` |
| REQ-HR1-6 | A hook saved for one user never runs in `run_hooks` for another user. | `hooks.run_hooks` |
| REQ-HR1-7 | A disabled hook (`.py.off`, or after `toggle_hook`) does not run; toggling again re-enables it. | `hooks.toggle_hook`, `hooks.run_hooks` |
| REQ-HR1-8 | A hook that raises, or that uses a builtin outside the spec's allow-list, makes `run_hooks` raise `HookError` whose `.hook` is that hook's name. | `hooks.run_hooks` |
| REQ-HR1-9 | Inside a hook, `ctx.user` is the user id, `ctx.api.get(path)` delegates to the given `api`, `ctx.log` lines appear in `ctx.logs`, and `date`, `datetime`, `timedelta` and `math` are available. | `hooks.run_hooks`, `hooks.Ctx` |

### PX1 — Proxy

| Id | Requirement | Checked through |
|---|---|---|
| REQ-PX1-1 | A request to a path outside `/fitit/` and `/x/` returns the same status and JSON body the demo app returns for it with the same cookies. | Core HTTP |
| REQ-PX1-2 | `GET /login?user=gerente` through the core returns the app's `set-cookie`, and the cookie then authenticates later requests through the core. | Core HTTP |
| REQ-PX1-3 | A matching enabled hook of the signed-in user changes the JSON body, and the response carries `x-fitit-hooks` with its name. | Core HTTP, hook files |
| REQ-PX1-4 | The same request by another user, or anonymously, returns the app's original body without `x-fitit-hooks`. | Core HTTP, hook files |
| REQ-PX1-5 | When a matching hook raises, the response is `502` with `{"error": "hook_failed", "hook": <name>, "detail": ...}` and contains none of the app's original data. | Core HTTP, hook files |
| REQ-PX1-6 | `GET /` as a signed-in user contains `<script src="/fitit/embed.js" defer></script>` exactly once, before `</body>`; anonymously it does not contain it. | Core HTTP |
| REQ-PX1-7 | `GET /x/<name>` returns `401` anonymously, `404` without a matching hook, and the hook's result with `200` otherwise, without any request reaching the app for that path. | Core HTTP, hook files |
| REQ-PX1-8 | `GET /fitit/hooks`, `POST /fitit/hooks/{name}/toggle` and `DELETE /fitit/hooks/{name}` behave as the spec says, including `401` and `404`, and a toggled-off hook is no longer applied. | Core HTTP |
| REQ-PX1-9 | `GET /fitit/embed.js` returns `200` with `content-type` `application/javascript`. | Core HTTP |
| REQ-PX1-10 | Each proxied request emits one `request.proxied` event, each applied hook one `hook.applied`, and each failure one `hook.failed`, with the fields of the trace spec. | Core HTTP, recorded `trace.emit` |

### AG1 — Coding agent

| Id | Requirement | Checked through |
|---|---|---|
| REQ-AG1-1 | `WS /fitit/ws` without a session is closed with code `4401`. | Core WebSocket |
| REQ-AG1-2 | For each message, the last event is exactly one `done`, and a model's final text arrives as one `text` event. | Core WebSocket, `FunctionModel` |
| REQ-AG1-3 | A `tool` event with the tool name and input is sent before each tool call, in call order. | Core WebSocket, `FunctionModel` |
| REQ-AG1-4 | `list_endpoints` returns one `METHOD path: summary` line per operation of the demo app's OpenAPI document. | Core WebSocket, `FunctionModel` |
| REQ-AG1-5 | `write_hook` with valid code saves the hook enabled for the connected user only, and sends `hook_saved` with the route and a unified diff. | Core WebSocket, file system |
| REQ-AG1-6 | `write_hook` with code the guard rejects saves nothing, sends `hook_rejected` with the reason, and returns the reason to the model as a retry. | Core WebSocket, `FunctionModel` |
| REQ-AG1-7 | `write_hook` with code that raises during the dry run on live data saves nothing and sends `hook_rejected`. | Core WebSocket, file system |
| REQ-AG1-8 | After three rejected attempts in one message, the turn sends `error` and then `done`, and nothing is saved. | Core WebSocket, `FunctionModel` |

### EW1 — Embed widget (checked manually by a human in a browser)

| Id | Requirement | Checked through |
|---|---|---|
| REQ-EW1-1 | On the demo page through the core, a floating button opens a panel, and the page's own styles are unchanged. | Browser |
| REQ-EW1-2 | Sending "Apply a 15% discount to Friday invoices" shows tool steps, then a green/red diff, then the answer, and the page reloads with discounted Friday invoices. | Browser, real model |
| REQ-EW1-3 | The hook list shows the new hook; toggling it off and on removes and restores the discount after reload. | Browser |
| REQ-EW1-4 | Deleting the hook removes it from the list and the discount from the page. | Browser |
| REQ-EW1-5 | The panel is usable at 360 px width and uses no `alert`, `confirm` or `prompt`. | Browser |

### TR1 — Trace sink (Álvaro)

| Id | Requirement | Checked through |
|---|---|---|
| REQ-TR1-1 | `trace.emit` never raises, including with fields that are not JSON-serializable. | `trace.emit` |
| REQ-TR1-2 | Every emitted event is recorded with `ts`, `kind` and its fields, and can be read back in emission order. | Traceability's own surface |
| REQ-TR1-3 | `trace.emit` returns in under 5 ms on average over 1,000 calls. | `trace.emit` |

## What is verified, and how

| Surface | Items | Check |
|---|---|---|
| Shell, Docker | OP1 | Tester runs the commands |
| Demo HTTP | DA1 | `TestClient` on `demo.main.app`, or HTTP to `demo:8001` |
| `backend/hooks.py` | HR1 | Imported functions, temporary `WORKSPACES` |
| Core HTTP / WebSocket | PX1, AG1 | `TestClient` on `main.app`, demo service as the app, `FunctionModel` |
| Browser | EW1 | Manual, by Martín |

## Closed to the tester

The developer's branch and pull request, every file under `backend/` and `demo/` except what the contracts name, and `tests/unit/`.
