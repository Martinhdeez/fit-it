# 0015. Widget as a static Next.js export in a same-origin iframe

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín

## Context

Embed widget v1 was one plain-JS file rendering the chat inside a Shadow DOM. The widget is growing (chat stream, diffs, hook list) and a teammate builds it in Next.js. The core must stay a single Python service and the client app must stay unmodified ([ADR 0002](0002-reverse-proxy-in-front-of-unmodified-app.md)).

## Decision

The widget UI is a Next.js static export (`output: "export"`, `basePath: "/fitit/ui"`) that the core serves with `StaticFiles` at `/fitit/ui/`. The injected `embed.js` becomes a ~20-line loader that adds a same-origin iframe pointing at `/fitit/ui/` and resizes it on `{fitit: "open" | "close"}` postMessages from that iframe only.

## Consequences

- The app's CSS and JS cannot touch the widget, and the widget cannot touch the app, without Shadow DOM tricks.
- Same origin: the iframe carries the app's session cookie, so `/fitit/ws` and `/fitit/hooks` authenticate as before. No new auth.
- No Node at runtime; the build output lands in `backend/static/ui/` (gitignored). The Docker image does not build it yet; a Node build stage comes when the frontend reaches `develop`.
- When the UI is not built, `/fitit/ui/*` is a 404 and the loader shows an empty 64 px frame.
- Reloading the app needs `window.top.location.reload()` from inside the iframe.

## Alternatives considered

- **Keep the single plain-JS file (v1):** fine for a button, painful for the UI the widget now needs.
- **Bundle the React app into `embed.js` and mount it in a Shadow DOM:** the app's global JS and fonts still leak in, and the bundle would need a custom build instead of a standard Next export.
- **Run Next.js as its own service:** a second origin (cookies, CORS) or another proxy hop, for no runtime gain over static files.
