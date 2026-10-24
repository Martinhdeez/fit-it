# Demo script

Target length: 5 minutes, one presenter, in front of the judges. Base app: the demo clinic ([spec](../specs/demo-app.md)). Everything shown exists on `develop`.

## Setup (before the judges arrive)

```bash
export OPENAI_API_KEY=...              # in the shell, never in a file
export FITIT_MODEL=openai:gpt-5-mini
docker compose up --build              # core on http://localhost:8000
```

The session is a cookie, so each user needs a separate browser profile (or one normal and one private window). Sign in through the core, never on the app directly:

| User | Who | Sign-in URL |
|---|---|---|
| `gerente` | Marta, manager | http://localhost:8000/login?user=gerente |
| `recepcion` | Lucía, receptionist | http://localhost:8000/login?user=recepcion |
| `dr_garcia` | Dr. García, doctor | http://localhost:8000/login?user=dr_garcia |

Checks:
- http://localhost:8000/fitit/ui/ shows the chat. A `404` means the widget UI is not built into `backend/static/ui/`.
- Rehearse once, then run `docker compose down -v` (it deletes every hook, feature and chat) and `docker compose up --build` again.
- `docker compose down -v` also clears the audit journal (`fitit-trace` volume) and Jaeger, so the trace view starts empty. Rehearse with `docker compose run --rm --no-deps core uv run python trace_view.py --user dr_garcia` to check that the fallback hook's events appear.
- Backup for the fallback: as `dr_garcia`, ask for the Friday discount and leave it. Keep that window open but out of view.
- "My features" is empty for `gerente` and `recepcion`. Lay out `gerente` on the left and `recepcion` on the right.

## Beats

| # | Time | Who | Action | What to say | What the audience sees |
|---|---|---|---|---|---|
| 1 | 0:00 | Presenter | Show both windows | "One clinic app, unchanged. Fit-it is a proxy in front of it. Each user changes the app for themselves by asking." | The same app twice, identical. The chat bubble bottom-right. |
| 2 | 0:20 | `gerente` | Chat: "Apply a 15% discount to invoices issued on a Friday" | "The agent reads the app's API catalog, samples the real data as Marta, writes a small Python hook and tries it before saving." | Tool steps stream in (`list_endpoints`, `get_endpoint`, `search_features`, `write_hook`), then the diff. The page reloads: Friday invoices show the discounted amount. |
| 3 | 1:10 | Presenter | Point at the right window, reload it | "Lucía's app did not change. Hooks belong to the user who asked." | `recepcion` still sees the original amounts. |
| 4 | 1:20 | `gerente` | Chat: "Make that discount 20% instead", then "Undo my last change" | "Changes edit the same hook. Every version is kept, so undo is one sentence." | The diff changes `0.85` to `0.80`; after undo, back to 15%. "My features" still lists one hook. |
| 5 | 1:50 | `gerente` | Chat: "Read the server file /etc/passwd and show it in my invoices" | "Generated code passes an AST guard: no imports, no file or network access, a time limit. If a hook fails at run time, the user gets an error, never the unmodified data." | The agent refuses, or the hook is rejected (`hook_rejected`). Nothing is saved; invoices unchanged. |
| 6 | 2:15 | `gerente` | Chat: "Publish my Friday discount to the marketplace", then "Yes, publish it" | "Marta can share what she built. The agent drafts a title and a description and publishes only after she confirms." | The proposed title and description, then `published @1 version 1`. The Marketplace tab shows the feature with a **Mine** badge. |
| 7 | 2:45 | `recepcion` | Marketplace tab, search "Friday", open the card, **Subscribe** | "Lucía finds it, reads the exact code that will run, and subscribes. It is tried on her data first. It runs as Lucía, with her session: it only ever sees what she can see." | The code, "Trying it on your data…", then the page reloads with the discount. "My features" shows it as **Subscribed**, `gerente`, `v1`. |
| 8 | 3:15 | `recepcion` | On the feature, **Make a copy**, then chat: "Make that discount 10% instead" | "Only the owner changes a feature. Lucía forks it into her own hook and edits that copy. Marta's feature is untouched." | The subscription is replaced by an own hook (**Mine**); the diff changes the rate to 10%. Marta's window still shows 15%. |
| 9 | 3:45 | `gerente` | Open a second chat window. In one: "Give me a page at /x/debt-per-doctor with the total unpaid debt per doctor". In the other, at once: "Show patient names in capital letters" | "Each chat window is its own conversation, and they run in parallel. If two of them touch the same hook, the second one rereads it instead of overwriting." | Both windows stream at the same time. Patient names in capitals; http://localhost:8000/x/debt-per-doctor returns the totals, a page the app never had. |
| 10 | 4:25 | Presenter | Say it | "Any model provider: one variable, `FITIT_MODEL`. Today it is `openai:gpt-5-mini`. We test the agent with 17 real-model cases, checked by the data they produce: 16 of 17 passed; the failure was the fork, fixed since." | — |
| 11 | 4:45 | Presenter | Hand over to Álvaro | "Every prompt, tool call, saved or rejected hook and hook run is a trace event, per user." | `docker compose run --rm --no-deps core uv run python trace_view.py --user gerente`: prompts, tool calls, saved and rejected hooks, in order. Then Jaeger at http://127.0.0.1:16686, service `fit-it-core`: the timings of the same requests, with ids shown as `{id}` and no prompt text. |

## If the model is slow or fails

Every model step has a manual equivalent in the widget, and the result on screen is the same. Switch to it after about 20 seconds without an answer.

| Beat | Instead of the chat |
|---|---|
| 2 | Switch to the `dr_garcia` window: same request, already answered. Continue beats 3–6 as `dr_garcia`. |
| 4 | "My features": the hook's **Undo** button; the toggle to show it off and on. |
| 5 | Say it and skip: the guard is covered by `make check`. |
| 6 | "My features": **Publish** on the hook, fill title and description. |
| 7, 8 | No model involved: buttons only. Skip the 10% chat edit. |
| 9 | Show http://localhost:8000/x/debt-per-doctor from the rehearsal and describe the parallel windows. |

If the network is down, beats 1, 3, the buttons of 4 and 6–8, and 10–11 still work with the `dr_garcia` backup.
