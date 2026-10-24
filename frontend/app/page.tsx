"use client";

import { useEffect, useRef, useState } from "react";
import {
  deleteHook,
  listHooks,
  openSocket,
  parseEvent,
  reloadHostPage,
  sendMessage,
  SignedOut,
  tellHost,
  toggleHook,
  UNAUTHENTICATED,
  type AgentEvent,
  type Hook,
} from "../lib/core";

type Connection = "idle" | "connecting" | "open" | "lost" | "signed-out";

type Item =
  | { kind: "prompt"; text: string }
  | { kind: "tool"; name: string }
  | { kind: "saved"; name: string; route: string; diff: string }
  | { kind: "rejected"; name: string; reason: string }
  | { kind: "answer"; text: string }
  | { kind: "error"; message: string };

const SIGN_IN = "Sign in to the app first";

type Size = { width: number; height: number };

const SIZE_KEY = "fitit-panel-size";
const MIN_SIZE: Size = { width: 360, height: 420 };
const STEP = 24;

function clampSize(size: Size): Size {
  const host = window.parent;
  const clamp = (value: number, min: number, max: number) => Math.round(Math.min(Math.max(value, min), Math.max(min, max)));
  return {
    width: clamp(size.width, MIN_SIZE.width, host.innerWidth - 16),
    height: clamp(size.height, MIN_SIZE.height, host.innerHeight - 16),
  };
}

function storedSize(): Size | null {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(SIZE_KEY) ?? "null");
    if (typeof value !== "object" || value === null) return null;
    const { width, height } = value as Record<string, unknown>;
    return typeof width === "number" && typeof height === "number" ? { width, height } : null;
  } catch {
    return null;
  }
}

function diffLineClass(line: string): string | undefined {
  if (line.startsWith("+") && !line.startsWith("+++")) return "add";
  if (line.startsWith("-") && !line.startsWith("---")) return "del";
  return undefined;
}

export default function Page() {
  const [open, setOpen] = useState(false);
  const [connection, setConnection] = useState<Connection>("idle");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");
  const [items, setItems] = useState<Item[]>([]);
  const [hooks, setHooks] = useState<Hook[]>([]);
  const [hooksError, setHooksError] = useState("");
  const [pendingHook, setPendingHook] = useState("");
  const socketRef = useRef<WebSocket | null>(null);
  const savedThisTurn = useRef(false);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ x: number; y: number; size: Size; last: Size } | null>(null);
  const requestedSize = useRef<Size | null>(null);

  useEffect(() => () => socketRef.current?.close(), []);

  useEffect(() => {
    if (open && connection === "open" && !busy) composerRef.current?.focus();
  }, [open, connection, busy]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [items]);

  const add = (item: Item) => setItems((current) => [...current, item]);

  async function loadHooks() {
    try {
      setHooks(await listHooks());
      setHooksError("");
    } catch (error) {
      if (error instanceof SignedOut) setConnection("signed-out");
      else setHooksError("Could not load your changes.");
    }
  }

  function handle(event: AgentEvent) {
    switch (event.type) {
      case "tool":
        add({ kind: "tool", name: event.name });
        return;
      case "hook_saved":
        savedThisTurn.current = true;
        add({ kind: "saved", name: event.name, route: event.route, diff: event.diff });
        return;
      case "hook_rejected":
        add({ kind: "rejected", name: event.name, reason: event.reason });
        return;
      case "text":
        add({ kind: "answer", text: event.text });
        return;
      case "error":
        add({ kind: "error", message: event.message });
        return;
      case "done":
        setBusy(false);
        if (savedThisTurn.current) {
          reloadHostPage();
          return;
        }
        void loadHooks();
    }
  }

  function connect() {
    const current = socketRef.current;
    if (current && current.readyState <= WebSocket.OPEN) return;

    const socket = openSocket();
    socketRef.current = socket;
    setConnection("connecting");
    socket.addEventListener("open", () => setConnection("open"));
    socket.addEventListener("message", (message) => {
      if (typeof message.data !== "string") return;
      const event = parseEvent(message.data);
      if (event) handle(event);
    });
    socket.addEventListener("close", (close) => {
      if (socketRef.current === socket) socketRef.current = null;
      setBusy(false);
      setConnection(close.code === UNAUTHENTICATED ? "signed-out" : "lost");
    });
  }

  function openPanel() {
    tellHost({ fitit: "open" });
    requestedSize.current = null;
    const size = storedSize();
    if (size) resizeTo(size, false);
    setOpen(true);
    void loadHooks();
    if (connection !== "signed-out") connect();
  }

  function closePanel() {
    setOpen(false);
    tellHost({ fitit: "close" });
  }

  // The host applies resize messages asynchronously, so the iframe's own size can lag behind.
  function currentSize(): Size {
    return requestedSize.current ?? { width: window.innerWidth, height: window.innerHeight };
  }

  function resizeTo(size: Size, remember: boolean): Size {
    const next = clampSize(size);
    requestedSize.current = next;
    tellHost({ fitit: "resize", ...next });
    if (remember) localStorage.setItem(SIZE_KEY, JSON.stringify(next));
    return next;
  }

  function resetSize() {
    localStorage.removeItem(SIZE_KEY);
    requestedSize.current = null;
    tellHost({ fitit: "open" });
  }

  function onResizeKey(event: React.KeyboardEvent) {
    const { width, height } = currentSize();
    const moves: Record<string, Size> = {
      ArrowLeft: { width: width + STEP, height },
      ArrowRight: { width: width - STEP, height },
      ArrowUp: { width, height: height + STEP },
      ArrowDown: { width, height: height - STEP },
    };
    if (event.key === "Home") {
      event.preventDefault();
      resetSize();
      return;
    }
    const next = moves[event.key];
    if (!next) return;
    event.preventDefault();
    resizeTo(next, true);
  }

  function send() {
    const text = draft.trim();
    const socket = socketRef.current;
    if (!text || busy || connection !== "open" || !socket) return;
    savedThisTurn.current = false;
    add({ kind: "prompt", text });
    setDraft("");
    setBusy(true);
    sendMessage(socket, text);
  }

  async function changeHook(name: string, action: (name: string) => Promise<void>) {
    setPendingHook(name);
    try {
      await action(name);
      reloadHostPage();
    } catch (error) {
      if (error instanceof SignedOut) setConnection("signed-out");
      else setHooksError("That change did not go through. Try again.");
      setPendingHook("");
    }
  }

  if (!open) {
    return (
      <main className="shell">
        <button type="button" className="launcher" aria-expanded={false} onClick={openPanel}>
          <span className="launcher-mark" aria-hidden="true" />
          Fit-it
        </button>
      </main>
    );
  }

  const signedOut = connection === "signed-out";
  const locked = busy || connection !== "open";
  const status = signedOut
    ? SIGN_IN
    : connection === "connecting"
      ? "Connecting…"
      : connection === "lost"
        ? "Connection lost. Close and reopen Fit-it to retry."
        : busy
          ? "Working…"
          : "";

  return (
    <main className="shell">
      <section
        className="panel"
        aria-label="Fit-it"
        onKeyDown={(event) => {
          if (event.key === "Escape") closePanel();
        }}
      >
        <button
          type="button"
          className="resize-handle"
          aria-label="Resize panel"
          title="Drag to resize. Arrow keys resize, Home or double-click resets."
          onKeyDown={onResizeKey}
          onDoubleClick={resetSize}
          onPointerDown={(event) => {
            if (event.button !== 0) return;
            event.preventDefault();
            try {
              event.currentTarget.setPointerCapture(event.pointerId);
            } catch {}
            const size = currentSize();
            dragRef.current = { x: event.screenX, y: event.screenY, size, last: size };
          }}
          onPointerMove={(event) => {
            const drag = dragRef.current;
            if (!drag) return;
            drag.last = resizeTo(
              {
                width: drag.size.width + drag.x - event.screenX,
                height: drag.size.height + drag.y - event.screenY,
              },
              false,
            );
          }}
          onPointerUp={() => {
            const drag = dragRef.current;
            if (!drag) return;
            dragRef.current = null;
            localStorage.setItem(SIZE_KEY, JSON.stringify(drag.last));
          }}
          onPointerCancel={() => {
            dragRef.current = null;
          }}
        >
          <svg viewBox="0 0 12 12" aria-hidden="true">
            <path d="M1 7l6-6M1 11L11 1" />
          </svg>
        </button>
        <header className="panel-header">
          <div>
            <h1>Fit-it</h1>
            <p>Change this app for you, in plain words.</p>
          </div>
          <button type="button" className="icon-button" aria-label="Close Fit-it" onClick={closePanel}>
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        </header>

        <div className="transcript" role="log" aria-live="polite" aria-relevant="additions">
          {items.length === 0 && !signedOut ? (
            <p className="empty">Describe a change to this app. For example: 15% discount on Friday invoices.</p>
          ) : null}
          {items.map((item, index) => {
            switch (item.kind) {
              case "prompt":
                return (
                  <p className="bubble user" key={index}>
                    {item.text}
                  </p>
                );
              case "tool":
                return (
                  <p className="tool" key={index}>
                    → {item.name}
                  </p>
                );
              case "saved":
                return (
                  <figure className="diff" key={index}>
                    <figcaption>
                      <span>{item.name}</span>
                      <code>{item.route}</code>
                    </figcaption>
                    <pre>
                      {item.diff.split("\n").map((line, lineIndex) => (
                        <span className={diffLineClass(line)} key={lineIndex}>
                          {line}
                          {"\n"}
                        </span>
                      ))}
                    </pre>
                  </figure>
                );
              case "rejected":
                return (
                  <p className="error" key={index}>
                    {item.name ? `${item.name}: ` : ""}
                    {item.reason}
                  </p>
                );
              case "answer":
                return (
                  <p className="bubble agent" key={index}>
                    {item.text}
                  </p>
                );
              case "error":
                return (
                  <p className="error" key={index}>
                    {item.message}
                  </p>
                );
            }
          })}
          <div ref={bottomRef} />
        </div>

        {!signedOut && (hooks.length > 0 || hooksError) ? (
          <div className="hooks">
            <h2>Your changes</h2>
            {hooksError ? <p className="error">{hooksError}</p> : null}
            <ul>
              {hooks.map((hook) => (
                <li key={hook.name}>
                  <label className="switch">
                    <input
                      type="checkbox"
                      role="switch"
                      checked={hook.enabled}
                      disabled={pendingHook !== ""}
                      onChange={() => void changeHook(hook.name, toggleHook)}
                    />
                    <span className="hook-text">
                      <span className="hook-name">{hook.name}</span>
                      <code>{hook.route}</code>
                    </span>
                  </label>
                  <button
                    type="button"
                    className="delete"
                    aria-label={`Delete ${hook.name}`}
                    disabled={pendingHook !== ""}
                    onClick={() => void changeHook(hook.name, deleteHook)}
                  >
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <p className="status" role="status">
          {status}
        </p>

        <form
          className="composer"
          onSubmit={(event) => {
            event.preventDefault();
            send();
          }}
        >
          <textarea
            ref={composerRef}
            aria-label="Describe a change to this app"
            placeholder="Describe a change…"
            rows={1}
            value={draft}
            disabled={locked}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                send();
              }
            }}
          />
          <button type="submit" className="send" aria-label="Send" disabled={locked || draft.trim() === ""}>
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M8 13V3M3.5 7.5L8 3l4.5 4.5" />
            </svg>
          </button>
        </form>
      </section>
    </main>
  );
}
