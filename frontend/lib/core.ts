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
  | { type: "feature_changed"; name: string; route: string; diff: string }
  | { type: "feature_changed" }
  | { type: "done" };

export type ConversationSummary = {
  id: string;
  title: string;
  updatedAt: string | null;
  turns: number | null;
};

export const UNAUTHENTICATED = 4401;

export class SignedOut extends Error {}

export class HookGone extends Error {}

export class UndoFailed extends Error {}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: string;

  constructor(status: number, code: string, detail: string) {
    super(detail || apiFallback(code, status));
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

function apiFallback(code: string, status: number): string {
  switch (code) {
    case "own_feature":
      return "This feature is already yours.";
    case "already_subscribed":
      return "You are already subscribed.";
    case "name_taken":
      return "That name is already in use.";
    case "up_to_date":
      return "Already up to date.";
    case "dry_run_failed":
      return "It does not work with your data.";
    case "forbidden":
      return "You cannot change this feature.";
    case "not_found":
      return "That feature is gone.";
    case "invalid":
      return "Check the form and try again.";
    default:
      return `Request failed (${status})`;
  }
}

export function errorText(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.detail) return error.detail;
    if (error.code && error.message !== `Request failed (${error.status})`) return error.message;
    return fallback;
  }
  if (error instanceof UndoFailed && error.message) return error.message;
  return fallback;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function idStr(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return "";
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
    case "feature_changed": {
      const name = value.name;
      const route = value.route;
      const diff = value.diff;
      if (typeof name === "string" && typeof route === "string" && typeof diff === "string") {
        return { type: "feature_changed", name, route, diff };
      }
      return { type: "feature_changed" };
    }
    case "done":
      return { type: "done" };
    default:
      return null;
  }
}

export function openSocket(conversationId?: string): WebSocket {
  const url = new URL("/fitit/ws", window.location.origin);
  url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  if (conversationId) url.searchParams.set("conversation", conversationId);
  return new WebSocket(url);
}

export function sendMessage(socket: WebSocket, text: string) {
  socket.send(JSON.stringify({ type: "message", text }));
}

async function apiError(response: Response): Promise<ApiError> {
  const payload: unknown = await response.json().catch(() => null);
  const code = isRecord(payload) ? str(payload.error) : "";
  const detail = isRecord(payload) ? str(payload.detail) : "";
  return new ApiError(response.status, code, detail);
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(path, { credentials: "same-origin", ...init });
  if (response.status === 401) throw new SignedOut();
  if (!response.ok) throw await apiError(response);
  return response;
}

async function postJson(path: string, body: unknown): Promise<Response> {
  return request(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
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

async function mutation(path: string, method: string): Promise<void> {
  const response = await fetch(path, { method, credentials: "same-origin" });
  if (response.status === 401) throw new SignedOut();
  if (response.status === 404) throw new HookGone();
  if (response.status === 409) {
    const body: unknown = await response.json().catch(() => null);
    throw new UndoFailed(isRecord(body) ? str(body.detail) : "");
  }
  if (!response.ok) throw await apiError(response);
}

export async function toggleHook(name: string): Promise<void> {
  await mutation(`/fitit/hooks/${encodeURIComponent(name)}/toggle`, "POST");
}

export async function deleteHook(name: string): Promise<void> {
  await mutation(`/fitit/hooks/${encodeURIComponent(name)}`, "DELETE");
}

export async function undoHook(name: string): Promise<void> {
  await mutation(`/fitit/hooks/${encodeURIComponent(name)}/undo`, "POST");
}

export async function clearHistory(conversationId?: string): Promise<void> {
  const path = conversationId
    ? `/fitit/history?conversation=${encodeURIComponent(conversationId)}`
    : "/fitit/history";
  await request(path, { method: "DELETE" });
}

const TURN_KEYS = ["turn_count", "turns", "turnCount"];
const ACTIVITY_KEYS = ["updated_at", "last_activity", "lastActivity", "updatedAt", "activity_at", "last_active_at"];

function turnCount(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value) && value >= 0) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    if (Number.isFinite(parsed) && parsed >= 0) return parsed;
  }
  return null;
}

function dateLike(value: unknown): string | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    const ms = value < 1e12 ? value * 1000 : value;
    const date = new Date(ms);
    return Number.isNaN(date.getTime()) ? null : date.toISOString();
  }
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  const date = new Date(trimmed);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function activityOf(record: Record<string, unknown>): string | null {
  const keys = new Set([
    ...ACTIVITY_KEYS,
    ...Object.keys(record).filter((key) => /updated|activity|active/i.test(key)),
  ]);
  for (const key of keys) {
    const parsed = dateLike(record[key]);
    if (parsed) return parsed;
  }
  return null;
}

function turnsOf(record: Record<string, unknown>): number | null {
  for (const key of TURN_KEYS) {
    if (!(key in record)) continue;
    const count = turnCount(record[key]);
    if (count !== null) return count;
  }
  return null;
}

function parseConversation(value: Record<string, unknown>): ConversationSummary | null {
  const id = idStr(value.id);
  if (!id) return null;
  const title = str(value.title).trim() || "New chat";
  return { id, title, updatedAt: activityOf(value), turns: turnsOf(value) };
}

function conversationRows(body: unknown): unknown[] | null {
  if (Array.isArray(body)) return body;
  if (!isRecord(body)) return null;
  if (Array.isArray(body.conversations)) return body.conversations;
  if (Array.isArray(body.items)) return body.items;
  return null;
}

export async function listConversations(): Promise<ConversationSummary[]> {
  try {
    const response = await fetch("/fitit/conversations", { credentials: "same-origin" });
    if (response.status === 401) throw new SignedOut();
    if (!response.ok) return [];
    const body: unknown = await response.json().catch(() => null);
    const rows = conversationRows(body);
    if (!rows) return [];
    const parsed = rows.flatMap((item) => {
      if (!isRecord(item)) return [];
      const conversation = parseConversation(item);
      return conversation ? [conversation] : [];
    });
    parsed.sort((a, b) => {
      if (a.updatedAt && b.updatedAt) return Date.parse(b.updatedAt) - Date.parse(a.updatedAt);
      if (a.updatedAt) return -1;
      if (b.updatedAt) return 1;
      return 0;
    });
    return parsed;
  } catch (error) {
    if (error instanceof SignedOut) throw error;
    return [];
  }
}

export async function transcribe(audio: Blob, language: string): Promise<string> {
  const url = `/fitit/transcribe?language=${encodeURIComponent(language)}`;
  const response = await fetch(url, {
    method: "POST",
    body: audio,
    credentials: "same-origin",
    headers: { "content-type": audio.type || "application/octet-stream" },
  });
  const payload: unknown = await response.json().catch(() => null);
  if (response.status === 401) throw new SignedOut();
  if (!response.ok) {
    const code = isRecord(payload) ? str(payload.error) : "";
    throw new Error(code || "transcription_failed");
  }
  return isRecord(payload) ? str(payload.text) : "";
}

export function reloadHostPage() {
  window.top?.location.reload();
}

export type HostMessage =
  | { fitit: "open" }
  | { fitit: "close" }
  | { fitit: "resize"; width: number; height: number }
  | { fitit: "resize-drag"; width: number; height: number; x: number; y: number };

export function tellHost(message: HostMessage) {
  window.parent.postMessage(message, window.location.origin);
}

export type OwnFeature = {
  source: "own";
  name: string;
  route: string;
  enabled: boolean;
  code: string;
  feature_id: string | null;
  published: boolean;
  published_version: number | null;
};

export type SubscribedFeature = {
  source: "subscribed";
  name: string;
  route: string;
  enabled: boolean;
  feature_id: string;
  title: string;
  owner: string;
  version: number;
  latest_version: number;
  update_available: boolean;
  published: boolean;
};

export type Feature = OwnFeature | SubscribedFeature;

export type MarketplaceSort = "subscribers" | "newest";

export type MarketplaceCard = {
  id: string;
  title: string;
  description: string;
  route: string;
  owner: string;
  version: number;
  subscribers: number;
  published_at: string;
  mine: boolean;
  subscribed: boolean;
};

export type MarketplaceFeature = MarketplaceCard & {
  published: boolean;
  code: string;
};

export function featureKey(feature: Feature): string {
  return feature.source === "own" ? `own:${feature.name}` : `sub:${feature.feature_id}`;
}

function optionalId(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  const id = idStr(value);
  return id === "" ? null : id;
}

function parseOwn(value: Record<string, unknown>): OwnFeature | null {
  const name = str(value.name);
  if (!name) return null;
  return {
    source: "own",
    name,
    route: str(value.route),
    enabled: value.enabled === true,
    code: str(value.code),
    feature_id: optionalId(value.feature_id),
    published: value.published === true,
    published_version: num(value.published_version),
  };
}

function parseSubscribed(value: Record<string, unknown>): SubscribedFeature | null {
  const featureId = idStr(value.feature_id);
  if (!featureId) return null;
  return {
    source: "subscribed",
    name: str(value.name) || `@${featureId}`,
    route: str(value.route),
    enabled: value.enabled === true,
    feature_id: featureId,
    title: str(value.title),
    owner: str(value.owner),
    version: num(value.version) ?? 0,
    latest_version: num(value.latest_version) ?? 0,
    update_available: value.update_available === true,
    published: value.published !== false,
  };
}

function parseFeature(value: Record<string, unknown>): Feature | null {
  if (value.source === "subscribed") return parseSubscribed(value);
  if (value.source === "own") return parseOwn(value);
  return null;
}

function ownFromHook(hook: Hook): OwnFeature {
  return {
    source: "own",
    name: hook.name,
    route: hook.route,
    enabled: hook.enabled,
    code: "",
    feature_id: null,
    published: false,
    published_version: null,
  };
}

export async function listFeatures(): Promise<Feature[]> {
  const response = await fetch("/fitit/features", { credentials: "same-origin" });
  if (response.status === 401) throw new SignedOut();
  // Draft routes live on another branch. Today's core only has /fitit/hooks.
  if (response.status === 404) return (await listHooks()).map(ownFromHook);
  if (!response.ok) throw await apiError(response);
  const body: unknown = await response.json();
  if (!Array.isArray(body)) return [];
  return body.flatMap((item) => {
    if (!isRecord(item)) return [];
    const feature = parseFeature(item);
    return feature ? [feature] : [];
  });
}

function marketplacePath(id: string, action = ""): string {
  const base = `/fitit/marketplace/features/${encodeURIComponent(id)}`;
  return action ? `${base}/${action}` : base;
}

export async function toggleSubscription(id: string): Promise<void> {
  await request(marketplacePath(id, "toggle"), { method: "POST" });
}

export async function unsubscribeFeature(id: string): Promise<void> {
  await request(marketplacePath(id, "unsubscribe"), { method: "POST" });
}

export async function acceptUpdate(id: string): Promise<void> {
  await request(marketplacePath(id, "accept-update"), { method: "POST" });
}

export async function publishFeature(hook: string, title: string, description: string): Promise<void> {
  await postJson("/fitit/marketplace/features", { hook, title, description });
}

export async function unpublishFeature(id: string): Promise<void> {
  await request(marketplacePath(id), { method: "DELETE" });
}

function parseCard(value: Record<string, unknown>): MarketplaceCard | null {
  const id = idStr(value.id);
  if (!id) return null;
  return {
    id,
    title: str(value.title),
    description: str(value.description),
    route: str(value.route),
    owner: str(value.owner),
    version: num(value.version) ?? 0,
    subscribers: num(value.subscribers) ?? 0,
    published_at: str(value.published_at),
    mine: value.mine === true,
    subscribed: value.subscribed === true,
  };
}

export async function listMarketplace(
  query: string,
  sort: MarketplaceSort,
  signal?: AbortSignal,
): Promise<MarketplaceCard[]> {
  const params = new URLSearchParams({ q: query, sort });
  const body: unknown = await (await request(`/fitit/marketplace/features?${params}`, { signal })).json();
  if (!Array.isArray(body)) return [];
  return body.flatMap((item) => {
    if (!isRecord(item)) return [];
    const card = parseCard(item);
    return card ? [card] : [];
  });
}

export async function getMarketplaceFeature(id: string): Promise<MarketplaceFeature> {
  const body: unknown = await (await request(marketplacePath(id))).json();
  if (!isRecord(body)) throw new ApiError(500, "", "");
  const card = parseCard({ ...body, id: idStr(body.id) || id });
  if (!card) throw new ApiError(500, "", "");
  return { ...card, published: body.published !== false, code: str(body.code) };
}

export async function subscribeFeature(id: string): Promise<void> {
  await request(marketplacePath(id, "subscribe"), { method: "POST" });
}

export async function forkFeature(id: string, name: string): Promise<void> {
  const trimmed = name.trim();
  await postJson(marketplacePath(id, "fork"), trimmed === "" ? {} : { name: trimmed });
}

export async function reportFeature(id: string, reason: string): Promise<void> {
  await postJson(marketplacePath(id, "report"), { reason });
}
