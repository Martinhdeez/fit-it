# Embed widget — v1

The script the core injects into every HTML page of a signed-in user. Served at `GET /fitit/embed.js`.

## Behaviour

1. Mounts a floating button in the bottom-right corner, inside a Shadow DOM root so the app's CSS cannot affect it and it cannot affect the app.
2. The button opens a panel with three areas: the conversation, an input box, and the list of the user's hooks.
3. Sending a message opens (or reuses) `WS /fitit/ws` on the same origin and sends `{"type": "message", "text": ...}`.
4. Events are rendered as they arrive:
   - `tool`: a small grey line, `→ <name>`;
   - `hook_saved`: the diff in a monospace block, added lines green, removed lines red;
   - `hook_rejected`: a red line with the reason;
   - `text`: the agent's answer;
   - `error`: a red line.
5. On `done` after at least one `hook_saved`, the page reloads so the change is visible.
6. The hook list comes from `GET /fitit/hooks`. Each row shows the name, the route, an on/off toggle (`POST /fitit/hooks/{name}/toggle`, then reload) and a delete button (`DELETE /fitit/hooks/{name}`, then reload).

## Constraints

- One file, plain JavaScript, no build step, no external requests.
- No `alert`, `confirm` or `prompt`.
- Readable at 360 px width.
