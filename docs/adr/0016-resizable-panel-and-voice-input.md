# 0016. User-resizable widget panel and voice input

- **Status:** Proposed
- **Date:** 2026-10-24
- **Deciders:** Miguel, Martín

## Context

[ADR 0015](0015-widget-as-static-iframe.md) puts the widget in a same-origin iframe and sizes that iframe from `{fitit: "open"}` and `{fitit: "close"}` only. A fixed open size clips a long diff, and typing every change is slow while the user is looking at the app.

`embed.js` ignores messages whose origin is not the page origin, and before this ADR it did not handle resize.

## Decision

The open panel is user-resizable. The iframe keeps posting `{fitit: "open"}` and `{fitit: "close"}`, and also posts `{fitit: "resize", width, height}` (`width` and `height` in pixels) to `location.origin`. `embed.js` sets the iframe to that size, clamped to the viewport minus 32 px.

Voice input records audio in the browser and POSTs that clip to the core. The core returns text; the widget does not transcribe in the browser.

## Consequences

- The `resize` message is in the sizing table of the [embed widget spec](../specs/embed-widget.md#sizing-contract).
- Posting stays on the page origin, as ADR 0015 requires.
- Dictation needs the core's transcription route. A failed or refused transcription leaves the draft as it was and shows an error. The clip is not kept in the widget after the request.

## Alternatives considered

- **Stay with the loader's fixed open size:** the panel cannot grow past that box, so diffs and the hook list clip.
- **Transcribe with the browser's speech API:** availability and quality vary by browser, and the audio never goes through the core.
