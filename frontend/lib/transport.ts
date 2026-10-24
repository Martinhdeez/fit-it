export type Hook = {
  id: string;
  name: string;
  enabled: boolean;
};

export type ClientMessage =
  | { type: "prompt"; text: string }
  | { type: "toggle"; id: string; enabled: boolean };

export type ServerMessage =
  | { type: "ready" }
  | { type: "step"; text: string }
  | { type: "diff"; id: string; name: string; patch: string }
  | { type: "error"; message: string }
  | { type: "hooks"; hooks: Hook[] };

export type Transport = {
  send: (message: ClientMessage) => void;
  close: () => void;
};

const FRIDAY_DIFF = `--- /dev/null
+++ b/friday_discount.py
@@ -0,0 +1,6 @@
+ROUTE = "/api/invoices"
+
+def apply(data, ctx):
+    for row in data:
+        if row["issued_on"].weekday() == 4:
+            row["amount"] = round(row["amount"] * 0.85, 2)
`;

const GUARD_ERROR =
  "The guard rejected this hook. Imports are not allowed. Nothing was saved.";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseHook(value: unknown): Hook | null {
  if (!isRecord(value)) return null;
  if (typeof value.id !== "string" || typeof value.name !== "string") return null;
  if (typeof value.enabled !== "boolean") return null;
  return { id: value.id, name: value.name, enabled: value.enabled };
}

export function parseServerMessage(raw: string): ServerMessage | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(value) || typeof value.type !== "string") return null;

  switch (value.type) {
    case "ready":
      return { type: "ready" };
    case "step":
      return typeof value.text === "string" ? { type: "step", text: value.text } : null;
    case "error":
      return typeof value.message === "string"
        ? { type: "error", message: value.message }
        : null;
    case "diff":
      if (
        typeof value.id !== "string" ||
        typeof value.name !== "string" ||
        typeof value.patch !== "string"
      ) {
        return null;
      }
      return { type: "diff", id: value.id, name: value.name, patch: value.patch };
    case "hooks": {
      if (!Array.isArray(value.hooks)) return null;
      const hooks: Hook[] = [];
      for (const item of value.hooks) {
        const hook = parseHook(item);
        if (!hook) return null;
        hooks.push(hook);
      }
      return { type: "hooks", hooks };
    }
    default:
      return null;
  }
}

function socketUrl(): string {
  const url = new URL("/fitit/ws", window.location.origin);
  url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

export function connectSocket(
  onMessage: (message: ServerMessage) => void,
  onDisconnect: () => void,
): Transport {
  const socket = new WebSocket(socketUrl());
  let closedByUs = false;

  socket.addEventListener("message", (event) => {
    if (typeof event.data !== "string") return;
    const message = parseServerMessage(event.data);
    if (message) onMessage(message);
  });
  socket.addEventListener("close", () => {
    if (!closedByUs) onDisconnect();
  });

  return {
    send(message) {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify(message));
      }
    },
    close() {
      closedByUs = true;
      socket.close();
    },
  };
}

export function connectFake(onMessage: (message: ServerMessage) => void): Transport {
  const timers = new Set<ReturnType<typeof setTimeout>>();
  const hooks: Hook[] = [];
  let nextId = 1;
  let closed = false;

  const later = (ms: number, run: () => void) => {
    const id = setTimeout(() => {
      timers.delete(id);
      if (!closed) run();
    }, ms);
    timers.add(id);
  };

  const emitHooks = () => {
    onMessage({ type: "hooks", hooks: hooks.map((hook) => ({ ...hook })) });
  };

  later(0, () => onMessage({ type: "ready" }));

  return {
    send(message) {
      if (closed) return;
      if (message.type === "toggle") {
        const hook = hooks.find((item) => item.id === message.id);
        if (!hook) return;
        hook.enabled = message.enabled;
        emitHooks();
        return;
      }

      if (message.text.includes("import os")) {
        later(600, () => onMessage({ type: "error", message: GUARD_ERROR }));
        later(900, () => onMessage({ type: "ready" }));
        return;
      }

      const id = `hook-${nextId}`;
      const name = nextId === 1 ? "Friday discount" : `Hook ${nextId}`;
      nextId += 1;
      later(400, () => onMessage({ type: "step", text: "Reading the API catalog" }));
      later(800, () => onMessage({ type: "step", text: "Writing a hook" }));
      later(1200, () => {
        hooks.push({ id, name, enabled: true });
        onMessage({ type: "diff", id, name, patch: FRIDAY_DIFF });
        emitHooks();
      });
      later(1500, () => onMessage({ type: "ready" }));
    },
    close() {
      closed = true;
      for (const id of timers) clearTimeout(id);
      timers.clear();
    },
  };
}

export function connect(
  onMessage: (message: ServerMessage) => void,
  onDisconnect: () => void,
): Transport {
  if (process.env.NEXT_PUBLIC_FITIT_TRANSPORT === "fake") {
    return connectFake(onMessage);
  }
  return connectSocket(onMessage, onDisconnect);
}
