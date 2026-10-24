"use client";

import { useEffect, useRef, useState } from "react";
import { connect, type Hook, type ServerMessage, type Transport } from "../lib/transport";

type Phase = "offline" | "ready" | "busy";

type TranscriptItem =
  | { kind: "prompt"; text: string }
  | { kind: "step"; text: string }
  | { kind: "diff"; id: string; name: string; patch: string }
  | { kind: "error"; message: string };

export default function Page() {
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>("offline");
  const [draft, setDraft] = useState("");
  const [items, setItems] = useState<TranscriptItem[]>([]);
  const [hooks, setHooks] = useState<Hook[]>([]);
  const transportRef = useRef<Transport | null>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;

    const onMessage = (message: ServerMessage) => {
      if (!active) return;
      if (message.type === "ready") {
        setPhase("ready");
        return;
      }
      if (message.type === "hooks") {
        setHooks(message.hooks);
        return;
      }
      if (message.type === "step") {
        setItems((current) => [...current, { kind: "step", text: message.text }]);
        return;
      }
      if (message.type === "diff") {
        setItems((current) => [
          ...current,
          { kind: "diff", id: message.id, name: message.name, patch: message.patch },
        ]);
        return;
      }
      setItems((current) => [...current, { kind: "error", message: message.message }]);
    };

    const transport = connect(onMessage, () => {
      if (active) setPhase("offline");
    });
    transportRef.current = transport;

    return () => {
      active = false;
      transport.close();
      transportRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (open) composerRef.current?.focus();
  }, [open]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [items, hooks]);

  function sendPrompt() {
    const text = draft.trim();
    if (!text || phase !== "ready") return;
    setItems((current) => [...current, { kind: "prompt", text }]);
    setDraft("");
    setPhase("busy");
    transportRef.current?.send({ type: "prompt", text });
  }

  if (!open) {
    return (
      <main className="canvas">
        <button type="button" className="launcher" aria-expanded={false} onClick={() => setOpen(true)}>
          Fit-it
        </button>
      </main>
    );
  }

  const composerDisabled = phase !== "ready";

  return (
    <main className="canvas">
      <section className="panel" aria-label="Fit-it">
        <header className="panel-header">
          <h1>Fit-it</h1>
          <button type="button" onClick={() => setOpen(false)}>
            Close
          </button>
        </header>
        <div className="transcript" role="log" aria-live="polite" aria-relevant="additions">
          {items.length === 0 ? <p className="empty">Describe a change to this app.</p> : null}
          {items.map((item, index) => {
            if (item.kind === "prompt") {
              return (
                <p className="prompt" key={index}>
                  {item.text}
                </p>
              );
            }
            if (item.kind === "step") {
              return (
                <p className="step" key={index}>
                  {item.text}
                </p>
              );
            }
            if (item.kind === "error") {
              return (
                <p className="error" key={index}>
                  {item.message}
                </p>
              );
            }
            return (
              <article className="diff" key={index}>
                <p>{item.name}</p>
                <pre>{item.patch}</pre>
              </article>
            );
          })}
          <div ref={bottomRef} />
        </div>
        {hooks.length > 0 ? (
          <ul className="hooks">
            {hooks.map((hook) => (
              <li key={hook.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={hook.enabled}
                    onChange={(event) => {
                      transportRef.current?.send({
                        type: "toggle",
                        id: hook.id,
                        enabled: event.target.checked,
                      });
                    }}
                  />
                  {hook.name}
                </label>
              </li>
            ))}
          </ul>
        ) : null}
        <p className="status" role="status">
          {phase === "offline" ? "Connecting" : phase === "busy" ? "Working" : ""}
        </p>
        <form
          className="composer"
          onSubmit={(event) => {
            event.preventDefault();
            sendPrompt();
          }}
        >
          <textarea
            ref={composerRef}
            aria-label="Describe a change to this app"
            rows={2}
            value={draft}
            disabled={composerDisabled}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                sendPrompt();
              }
            }}
          />
          <button type="submit" className="send" disabled={composerDisabled || draft.trim() === ""}>
            {phase === "busy" ? "Sending" : "Send"}
          </button>
        </form>
      </section>
    </main>
  );
}
