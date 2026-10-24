// Client for the Fit-it core. Contracts: docs/specs/agent.md and docs/specs/proxy.md.

export type Hook = {
  name: string;
  route: string;
  enabled: boolean;
};

export type AgentEvent =
  | { type: "tool"; name: string; input: unknown }
  | { type: "hook_saved"; name: string; route: string; diff: string }
  | { type: "hook_rejected"; name: string; reason: string }
  | { type: "text"; text: string }
  | { type: "error"; message: string }
  | { type: "done" };

export const UNAUTHENTICATED = 4401;

export class SignedOut extends Error {}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function parseEvent(raw: string): AgentEvent | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(value)) return null;

  switch (value.type) {
    case "tool":
      return { type: "tool", name: str(value.name), input: value.input };
    case "hook_saved":
      return { type: "hook_saved", name: str(value.name), route: str(value.route), diff: str(value.diff) };
    case "hook_rejected":
      return { type: "hook_rejected", name: str(value.name), reason: str(value.reason) };
    case "text":
      return { type: "text", text: str(value.text) };
    case "error":
      return { type: "error", message: str(value.message) };
    case "done":
      return { type: "done" };
    default:
      return null;
  }
}

export function openSocket(): WebSocket {
  const url = new URL("/fitit/ws", window.location.origin);
  url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return new WebSocket(url);
}

export function sendMessage(socket: WebSocket, text: string) {
  socket.send(JSON.stringify({ type: "message", text }));
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(path, { credentials: "same-origin", ...init });
  if (response.status === 401) throw new SignedOut();
  if (!response.ok) throw new Error(`${init?.method ?? "GET"} ${path} failed with ${response.status}`);
  return response;
}

export async function listHooks(): Promise<Hook[]> {
  const body: unknown = await (await request("/fitit/hooks")).json();
  if (!Array.isArray(body)) return [];
  return body.filter(isRecord).map((hook) => ({
    name: str(hook.name),
    route: str(hook.route),
    enabled: hook.enabled === true,
  }));
}

export async function toggleHook(name: string): Promise<void> {
  await request(`/fitit/hooks/${encodeURIComponent(name)}/toggle`, { method: "POST" });
}

export async function deleteHook(name: string): Promise<void> {
  await request(`/fitit/hooks/${encodeURIComponent(name)}`, { method: "DELETE" });
}

export function reloadHostPage() {
  window.top?.location.reload();
}

export type HostMessage =
  | { fitit: "open" }
  | { fitit: "close" }
  | { fitit: "resize"; width: number; height: number };

export function tellHost(message: HostMessage) {
  window.parent.postMessage(message, "*");
}
