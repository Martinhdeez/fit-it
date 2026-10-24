# E5 — Embed widget

- **Item:** EW1 · **Spec:** [embed-widget.md](../specs/embed-widget.md) · **Requirements:** REQ-EW1-1 … 5 (manual)
- **Depends on:** AG1

## Goal

The only UI of Fit-it: a chat panel inside the client's page, showing agent steps, diffs and the user's hooks.

## Stories

| # | Story | Acceptance |
|---|---|---|
| 1 | As a user, I open Fit-it from any page without breaking the app's look. | REQ-EW1-1 |
| 2 | As a user, I ask for a change and watch it being built, then see it applied. | REQ-EW1-2 |
| 3 | As a user, I switch my changes on and off, or remove them. | REQ-EW1-3, REQ-EW1-4 |
| 4 | As a user on a small screen, the panel still works. | REQ-EW1-5 |

## Deliverables

`backend/static/embed.js`, served by `GET /fitit/embed.js`.

## Out of scope

Markdown rendering, syntax highlighting beyond diff colours, a build step, frameworks.
