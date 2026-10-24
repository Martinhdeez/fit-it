# Demo script

Target length: 3 minutes. Base app: the demo clinic ([spec](../specs/demo-app.md)). Two browser windows side by side, logged in as two different users.

| # | Who | Action | What the audience sees |
|---|---|---|---|
| 1 | Presenter | Open the clinic through Fit-it as `recepcion` and as `gerente` | The same app twice, identical. "This app has not been changed." |
| 2 | `gerente` | Chat: "Apply a 15% discount to invoices issued on a Friday" | Agent steps stream in: reads the API catalog, samples `/api/invoices`, writes a hook, validates it, shows the diff |
| 3 | `gerente` | Page reloads | Friday invoices show the discounted amount. `recepcion`'s window is unchanged. |
| 4 | `recepcion` | Chat: "Hide the phone number of patients who owe money" | Phones masked only for this user |
| 5 | `gerente` | Chat: "Give me a page with total unpaid debt per doctor" | A new virtual endpoint `/x/debt-per-doctor` built by combining existing API calls; no route was added to the app |
| 6 | Presenter | Toggle a hook off in the widget | The change disappears instantly; toggle on, it returns |
| 7 | Presenter | Show the trace view | Every prompt, generated hook, validation and execution, with timings |
| 8 | Presenter | Ask the agent for something that needs `import os` | The guard rejects the hook; nothing is saved |

Fallback if the network or the model fails: hooks from a rehearsal are kept in `workspaces/` and steps 3, 6 and 7 still work offline from the model.
