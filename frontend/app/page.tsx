"use client";

import { useEffect, useRef, useState } from "react";
import {
  acceptUpdate,
  ApiError,
  conversationMessages,
  deleteHook,
  errorText,
  featureKey,
  HookGone,
  listConversations,
  listFeatures,
  openSocket,
  parseEvent,
  publishFeature,
  reloadHostPage,
  sendMessage,
  SignedOut,
  tellHost,
  toggleHook,
  toggleSubscription,
  UndoFailed,
  undoHook,
  unpublishFeature,
  unsubscribeFeature,
  transcribe,
  UNAUTHENTICATED,
  type AgentEvent,
  type ConversationSummary,
  type Feature,
  type OwnFeature,
} from "../lib/core";
import { canRecord, startRecording, type Recording } from "../lib/dictation";
import { Features } from "../components/features";
import { Marketplace } from "../components/marketplace";

type WindowConnection = "idle" | "connecting" | "open" | "lost";

type Item =
  | { kind: "prompt"; text: string }
  | { kind: "tool"; name: string }
  | { kind: "saved"; name: string; route: string; diff: string }
  | { kind: "rejected"; name: string; reason: string }
  | { kind: "answer"; text: string }
  | { kind: "error"; message: string }
  | { kind: "changed" };

type ChatWindow = {
  key: string;
  conversationId: string | null;
  title: string;
  items: Item[];
  draft: string;
  busy: boolean;
  connection: WindowConnection;
};

type WindowRuntime = {
  socket: WebSocket | null;
  savedThisTurn: boolean;
  historyLoaded: boolean;
};

function windowKey(conversationId: string | null): string {
  return conversationId && conversationId !== "default" ? `c:${conversationId}` : "default";
}

function createChat(conversationId: string | null, title = "New chat"): ChatWindow {
  return {
    key: windowKey(conversationId),
    conversationId,
    title: title.trim() || "New chat",
    items: [],
    draft: "",
    busy: false,
    connection: "idle",
  };
}

function chatLabel(chat: ChatWindow): string {
  return chat.title.trim() || "New chat";
}

const SIGN_IN = "Sign in to the app first";

const FALLBACK_SUGGESTIONS = [
  "Apply a 15% discount to invoices issued on a Friday",
  "Hide the phone number of patients who owe money",
  "Give me a page with total unpaid debt per doctor",
];

type Reply = Exclude<Item, { kind: "prompt" | "tool" }> | { kind: "tools"; names: string[] };

type Turn = { prompt: string | null; replies: Reply[] };

function toTurns(items: Item[]): Turn[] {
  const turns: Turn[] = [];
  for (const item of items) {
    if (item.kind === "prompt") {
      turns.push({ prompt: item.text, replies: [] });
      continue;
    }
    if (turns.length === 0) turns.push({ prompt: null, replies: [] });
    const replies = turns[turns.length - 1].replies;
    const last = replies[replies.length - 1];
    if (item.kind === "tool") {
      if (last?.kind === "tools") last.names.push(item.name);
      else replies.push({ kind: "tools", names: [item.name] });
      continue;
    }
    replies.push(item);
  }
  return turns;
}

function Mark() {
  return (
    <svg className="mark" viewBox="16 16 88 88" aria-hidden="true">
      <rect className="mark-spine" x="22" y="22" width="24" height="76" rx="12" />
      <path d="M52 22H92a6 6 0 0 1 6 6v18a6 6 0 0 1-6 6H52z" />
      <circle cx="64" cy="74" r="12" />
    </svg>
  );
}

const WAVE_BARS = 27;

function Waveform() {
  return (
    <span className="wave" aria-hidden="true">
      {Array.from({ length: WAVE_BARS }, (_, index) => {
        const level = (0.34 + 0.66 * Math.abs(Math.sin(index * 0.68 + 0.5))).toFixed(2);
        return (
          <span
            key={index}
            style={
              {
                "--peak": level,
                "--rest": level,
                animationDelay: `${(index % 9) * 0.08}s`,
              } as React.CSSProperties
            }
          />
        );
      })}
    </span>
  );
}

type Size = { width: number; height: number };

const SIZE_KEY = "fitit-panel-size";
const WINDOWS_KEY = "fitit-windows";
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
  const [closing, setClosing] = useState(false);
  const closingRef = useRef(false);
  const [windows, setWindows] = useState<ChatWindow[]>(() => [createChat(null)]);
  const [activeKey, setActiveKey] = useState("default");
  const [signedOut, setSignedOut] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [chatsOpen, setChatsOpen] = useState(false);
  const [sheet, setSheet] = useState<null | "features" | "market">(null);
  const [features, setFeatures] = useState<Feature[]>([]);
  const [suggestions, setSuggestions] = useState<string[] | null>(null);
  const [featuresError, setFeaturesError] = useState("");
  const [featuresLoaded, setFeaturesLoaded] = useState(false);
  const [pendingFeature, setPendingFeature] = useState("");
  const [micSupported, setMicSupported] = useState(false);
  const [dictating, setDictating] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [dictationKey, setDictationKey] = useState<string | null>(null);
  const [micError, setMicError] = useState("");
  const [micErrorKey, setMicErrorKey] = useState<string | null>(null);
  const runtime = useRef(new Map<string, WindowRuntime>());
  const windowsRef = useRef(windows);
  const activeKeyRef = useRef(activeKey);
  const signedOutRef = useRef(signedOut);
  const onEventRef = useRef<(key: string, event: AgentEvent) => void>(() => {});
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const activeTabRef = useRef<HTMLDivElement>(null);
  const tabRefs = useRef(new Map<string, HTMLButtonElement>());
  const chatsRootRef = useRef<HTMLDivElement>(null);
  const featuresTriggerRef = useRef<HTMLButtonElement>(null);
  const marketTriggerRef = useRef<HTMLButtonElement>(null);
  const sheetRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ x: number; y: number; size: Size; last: Size } | null>(null);
  const requestedSize = useRef<Size | null>(null);
  const recordingRef = useRef<Recording | null>(null);
  windowsRef.current = windows;
  activeKeyRef.current = activeKey;
  signedOutRef.current = signedOut;
  const active = windows.find((chat) => chat.key === activeKey) ?? windows[0];

  function patch(key: string, update: (chat: ChatWindow) => ChatWindow) {
    setWindows((current) => current.map((chat) => (chat.key === key ? update(chat) : chat)));
  }

  function runtimeFor(key: string): WindowRuntime {
    const found = runtime.current.get(key);
    if (found) return found;
    const created: WindowRuntime = { socket: null, savedThisTurn: false, historyLoaded: false };
    runtime.current.set(key, created);
    return created;
  }

  function append(key: string, item: Item) {
    patch(key, (chat) => ({ ...chat, items: [...chat.items, item] }));
  }

  useEffect(() => {
    function onHostResize(event: MessageEvent) {
      if (event.origin !== window.location.origin) return;
      const data: unknown = event.data;
      if (typeof data !== "object" || data === null || (data as { fitit?: string }).fitit !== "resized") return;
      const { width, height } = data as { width?: unknown; height?: unknown };
      if (typeof width !== "number" || typeof height !== "number") return;
      const size = { width, height };
      requestedSize.current = size;
      dragRef.current = null;
      localStorage.setItem(SIZE_KEY, JSON.stringify(size));
    }
    window.addEventListener("message", onHostResize);
    return () => window.removeEventListener("message", onHostResize);
  }, []);

  useEffect(() => {
    setMicSupported(canRecord());
    const slots = runtime.current;
    return () => {
      for (const slot of slots.values()) {
        const socket = slot.socket;
        slot.socket = null;
        socket?.close();
      }
      recordingRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (open && !signedOut && active.connection === "open" && !active.busy && !(transcribing && dictationKey === active.key)) {
      composerRef.current?.focus();
    }
  }, [open, signedOut, active.connection, active.busy, transcribing, dictationKey]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [active.items, active.key]);

  useEffect(() => {
    activeTabRef.current?.scrollIntoView({ inline: "nearest", block: "nearest" });
  }, [activeKey]);

  useEffect(() => {
    if (!chatsOpen) return;
    function onPointerDown(event: PointerEvent) {
      if (!chatsRootRef.current?.contains(event.target as Node)) setChatsOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [chatsOpen]);

  useEffect(() => {
    if (!sheet) return;
    sheetRef.current?.focus();
  }, [sheet]);

  // Restore the windows kept across the host reload that follows a saved hook (embed-widget spec, "Conversations").
  useEffect(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(WINDOWS_KEY) ?? "null");
      sessionStorage.removeItem(WINDOWS_KEY);
      if (!saved?.windows?.length) return;
      const restored: ChatWindow[] = saved.windows.map((chat: ChatWindow) => ({
        ...createChat(chat.conversationId, chat.title),
        items: chat.items,
      }));
      windowsRef.current = restored;
      setWindows(restored);
      activeKeyRef.current = saved.activeKey;
      setActiveKey(saved.activeKey);
      if (saved.open) openPanel();
    } catch {}
  }, []);

  function reloadKeepingWindows() {
    try {
      const windows = windowsRef.current.map(({ conversationId, title, items }) => ({ conversationId, title, items }));
      sessionStorage.setItem(WINDOWS_KEY, JSON.stringify({ open, activeKey: activeKeyRef.current, windows }));
    } catch {}
    reloadHostPage();
  }

  async function loadFeatures() {
    try {
      setFeatures(await listFeatures());
      setFeaturesError("");
    } catch (error) {
      if (error instanceof SignedOut) setSignedOut(true);
      else setFeaturesError("Could not load your changes.");
    } finally {
      setFeaturesLoaded(true);
    }
  }

  async function refreshConversations() {
    try {
      const list = await listConversations();
      setConversations(list);
      setWindows((current) =>
        current.map((chat) => {
          // The first window has no id; the server stores it as "default".
          const match = list.find((item) => item.id === (chat.conversationId ?? "default"));
          if (!match?.title || match.title === chat.title) return chat;
          return { ...chat, title: match.title };
        }),
      );
    } catch (error) {
      if (error instanceof SignedOut) setSignedOut(true);
      else setConversations([]);
    }
  }

  function onEvent(key: string, event: AgentEvent) {
    const slot = runtimeFor(key);
    switch (event.type) {
      case "tool":
        append(key, { kind: "tool", name: event.name });
        return;
      case "hook_saved":
        slot.savedThisTurn = true;
        append(key, { kind: "saved", name: event.name, route: event.route, diff: event.diff });
        return;
      case "feature_changed":
        slot.savedThisTurn = true;
        if ("name" in event) {
          append(key, { kind: "saved", name: event.name, route: event.route, diff: event.diff });
        } else {
          append(key, { kind: "changed" });
        }
        return;
      case "hook_rejected":
        append(key, { kind: "rejected", name: event.name, reason: event.reason });
        return;
      case "text":
        append(key, { kind: "answer", text: event.text });
        return;
      case "error":
        append(key, { kind: "error", message: event.message });
        return;
      case "done": {
        const reload = slot.savedThisTurn;
        patch(key, (chat) => ({ ...chat, busy: false }));
        if (reload) {
          reloadKeepingWindows();
          return;
        }
        void loadFeatures();
        void refreshConversations();
      }
    }
  }
  onEventRef.current = onEvent;

  // Show the turns the server stored for this conversation (page load, history list); server data wins over sessionStorage.
  async function loadMessages(key: string, conversationId: string | null) {
    const before = windowsRef.current.find((chat) => chat.key === key)?.items;
    try {
      const past = await conversationMessages(conversationId ?? "default");
      if (past.length === 0) return;
      const items: Item[] = past.map((m) => (m.role === "user" ? { kind: "prompt", text: m.text } : { kind: "answer", text: m.text }));
      patch(key, (chat) => (chat.items === before && !chat.busy ? { ...chat, items } : chat));
    } catch (error) {
      if (error instanceof SignedOut) setSignedOut(true);
    }
  }

  function connectWindow(key: string, conversationId: string | null) {
    if (signedOutRef.current) return;
    const slot = runtimeFor(key);
    if (!slot.historyLoaded) {
      slot.historyLoaded = true;
      void loadMessages(key, conversationId);
    }
    const current = slot.socket;
    if (current && current.readyState <= WebSocket.OPEN) return;

    const socket = openSocket(conversationId ?? undefined);
    slot.socket = socket;
    patch(key, (chat) => ({ ...chat, connection: "connecting" }));
    socket.addEventListener("open", () => {
      if (slot.socket !== socket) return;
      patch(key, (chat) => ({ ...chat, connection: "open" }));
    });
    socket.addEventListener("message", (message) => {
      if (slot.socket !== socket || typeof message.data !== "string") return;
      const event = parseEvent(message.data);
      if (event) onEventRef.current(key, event);
    });
    socket.addEventListener("close", (close) => {
      if (slot.socket !== socket) return;
      slot.socket = null;
      patch(key, (chat) => ({ ...chat, busy: false, connection: "lost" }));
      if (close.code === UNAUTHENTICATED) setSignedOut(true);
      // The core restarts or the network blips: retry, except for closes that a retry cannot fix (4400, 4403).
      else if (close.code !== 4400 && close.code !== 4403) setTimeout(() => connectWindow(key, conversationId), 2000);
    });
  }

  function openPanel() {
    closingRef.current = false;
    setClosing(false);
    tellHost({ fitit: "open" });
    requestedSize.current = null;
    const size = storedSize();
    if (size) resizeTo(size, false);
    setOpen(true);
    void loadFeatures();
    if (!suggestions) {
      fetch("/fitit/suggestions")
        .then((r) => (r.ok ? r.json() : Promise.reject()))
        .then(setSuggestions, () => setSuggestions(FALLBACK_SUGGESTIONS));
    }
    if (signedOutRef.current) return;
    for (const chat of windowsRef.current) connectWindow(chat.key, chat.conversationId);
    void refreshConversations();
  }

  function closePanel() {
    if (closingRef.current) return;
    cancelRecording();
    setChatsOpen(false);
    setSheet(null);
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setOpen(false);
      tellHost({ fitit: "close" });
      return;
    }
    closingRef.current = true;
    setClosing(true);
  }

  function finishClose(event: React.AnimationEvent<HTMLElement>) {
    if (event.target !== event.currentTarget || event.animationName !== "panel-out") return;
    if (!closingRef.current) return;
    closingRef.current = false;
    setClosing(false);
    setOpen(false);
    tellHost({ fitit: "close" });
  }

  function toggleSheet(next: "features" | "market") {
    setChatsOpen(false);
    setSheet((current) => (current === next ? null : next));
  }

  function closeSheet(restoreFocus = true) {
    const which = sheet;
    setSheet(null);
    if (!restoreFocus || !which) return;
    const trigger = which === "market" ? marketTriggerRef.current : featuresTriggerRef.current;
    requestAnimationFrame(() => trigger?.focus());
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

  function cancelRecording() {
    const recording = recordingRef.current;
    if (!recording) return;
    recordingRef.current = null;
    setDictating(false);
    setDictationKey(null);
    recording.abort();
  }

  function focusWindow(key: string, focusTab = false) {
    if (key !== activeKeyRef.current) {
      cancelRecording();
      activeKeyRef.current = key;
      setActiveKey(key);
    }
    setChatsOpen(false);
    if (focusTab) requestAnimationFrame(() => tabRefs.current.get(key)?.focus());
  }

  function showConversation(conversationId: string | null, title?: string) {
    const key = windowKey(conversationId);
    const existing = windowsRef.current.some((chat) => chat.key === key);
    if (!existing) {
      const next = [...windowsRef.current, createChat(conversationId, title)];
      windowsRef.current = next;
      setWindows(next);
    }
    focusWindow(key);
    if (!signedOutRef.current) connectWindow(key, conversationId);
  }

  function closeWindow(key: string) {
    const current = windowsRef.current;
    if (current.length < 2) return;
    const index = current.findIndex((chat) => chat.key === key);
    if (index < 0) return;
    if (dictationKey === key) cancelRecording();
    const slot = runtime.current.get(key);
    const socket = slot?.socket ?? null;
    if (slot) slot.socket = null;
    socket?.close();
    runtime.current.delete(key);
    const next = current.filter((chat) => chat.key !== key);
    windowsRef.current = next;
    if (activeKeyRef.current === key) {
      const neighbor = next[Math.max(0, index - 1)].key;
      activeKeyRef.current = neighbor;
      setActiveKey(neighbor);
    }
    setWindows(next);
  }

  function onTabsKey(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    const tabs = windowsRef.current;
    const index = tabs.findIndex((chat) => chat.key === activeKeyRef.current);
    if (index < 0) return;
    event.preventDefault();
    const next = event.key === "ArrowRight" ? (index + 1) % tabs.length : (index - 1 + tabs.length) % tabs.length;
    focusWindow(tabs[next].key, true);
  }

  function send() {
    const text = active.draft.trim();
    const socket = runtime.current.get(active.key)?.socket ?? null;
    const transcribingHere = transcribing && dictationKey === active.key;
    if (!text || active.busy || signedOut || transcribingHere || active.connection !== "open" || !socket) return;
    cancelRecording();
    runtimeFor(active.key).savedThisTurn = false;
    patch(active.key, (chat) => ({
      ...chat,
      draft: "",
      busy: true,
      // Title like the backend (first prompt, 60 chars) so a failed, unstored first turn is not left as "New chat".
      title: chat.items.some((item) => item.kind === "prompt") ? chat.title : [...text].slice(0, 60).join(""),
      items: [...chat.items, { kind: "prompt", text }],
    }));
    sendMessage(socket, text);
  }

  function toggleDictation() {
    if (recordingRef.current) {
      recordingRef.current.stop();
      return;
    }
    const key = active.key;
    const base = active.draft.trim() === "" ? "" : `${active.draft.trimEnd()} `;
    setMicError("");
    setMicErrorKey(key);
    setDictationKey(key);
    recordingRef.current = startRecording(
      (blob) => {
        recordingRef.current = null;
        setDictating(false);
        if (blob.size > 0) void finishDictation(key, base, blob);
        else setDictationKey(null);
      },
      (message) => {
        recordingRef.current = null;
        setDictating(false);
        setDictationKey(null);
        setMicError(message);
        setMicErrorKey(key);
      },
    );
    setDictating(true);
  }

  async function finishDictation(key: string, base: string, blob: Blob) {
    setTranscribing(true);
    try {
      const text = (await transcribe(blob, navigator.language)).trim();
      patch(key, (chat) => ({ ...chat, draft: text ? base + text : base.trimEnd() }));
      if (!text) {
        setMicError("No speech heard. Try again.");
        setMicErrorKey(key);
      }
    } catch (error) {
      setMicErrorKey(key);
      if (error instanceof SignedOut) setSignedOut(true);
      else if (error instanceof Error && error.message === "transcription_unavailable") {
        setMicError("Transcription is not set up on this server.");
      } else setMicError("Transcription failed. Try again.");
    } finally {
      setTranscribing(false);
      setDictationKey(null);
      if (activeKeyRef.current === key) composerRef.current?.focus();
    }
  }

  function dropOwn(name: string) {
    setFeatures((current) => current.filter((feature) => feature.source !== "own" || feature.name !== name));
  }

  function dropSubscribed(id: string) {
    setFeatures((current) => current.filter((feature) => feature.source !== "subscribed" || feature.feature_id !== id));
  }

  async function runFeature(key: string, action: () => Promise<void>, onGone?: () => void) {
    setPendingFeature(key);
    try {
      await action();
    } catch (error) {
      if (error instanceof SignedOut) setSignedOut(true);
      else if (error instanceof UndoFailed) setFeaturesError(error.message || "Nothing to undo.");
      else if (error instanceof HookGone || (error instanceof ApiError && error.status === 404)) {
        onGone?.();
        setFeaturesError(error instanceof ApiError && error.detail ? error.detail : "That feature is gone.");
      } else setFeaturesError(errorText(error, "That change did not go through. Try again."));
      setPendingFeature("");
      return;
    }
    reloadKeepingWindows();
  }

  async function publish(name: string, title: string, description: string) {
    setPendingFeature(`own:${name}`);
    try {
      await publishFeature(name, title, description);
      setFeaturesError("");
      await loadFeatures();
    } catch (error) {
      if (error instanceof SignedOut) {
        setSignedOut(true);
        throw new Error("Sign in to the app first");
      }
      throw new Error(errorText(error, "Could not publish this feature."));
    } finally {
      setPendingFeature("");
    }
  }

  async function unpublish(feature: OwnFeature) {
    if (!feature.feature_id) return;
    setPendingFeature(featureKey(feature));
    try {
      await unpublishFeature(feature.feature_id);
      setFeaturesError("");
      await loadFeatures();
    } catch (error) {
      if (error instanceof SignedOut) setSignedOut(true);
      else setFeaturesError(errorText(error, "Could not unpublish this feature."));
    } finally {
      setPendingFeature("");
    }
  }

  if (!open) {
    return (
      <main className="shell">
        <button type="button" className="launcher" aria-label="Open Fit-it" aria-expanded={false} onClick={openPanel}>
          <Mark />
        </button>
      </main>
    );
  }

  const transcribingHere = transcribing && dictationKey === active.key;
  const dictatingHere = dictating && dictationKey === active.key;
  const locked = signedOut || active.busy || transcribingHere || active.connection !== "open";
  const status = signedOut
    ? SIGN_IN
    : active.connection === "connecting"
      ? "Connecting…"
      : active.connection === "lost"
        ? "Connection lost. Reconnecting…"
        : transcribingHere
          ? "Transcribing…"
          : micErrorKey === active.key
            ? micError
            : "";
  const turns = toTurns(active.items);
  const enabledCount = features.filter((feature) => feature.enabled).length;
  const sheetTitle = sheet === "market" ? "Marketplace" : "My features";
  const sheetId = sheet === "market" ? "marketplace-sheet" : "features-sheet";
  const sheetHeadingId = sheet === "market" ? "marketplace-heading" : "features-heading";

  function pickSuggestion(text: string) {
    patch(active.key, (chat) => ({ ...chat, draft: text }));
    composerRef.current?.focus();
  }

  return (
    <main className="shell">
      <section
        className={closing ? "panel panel-out" : "panel"}
        onAnimationEnd={finishClose}
        aria-label="Fit-it"
        onKeyDown={(event) => {
          if (event.key !== "Escape") return;
          if (sheet) {
            closeSheet();
            return;
          }
          if (chatsOpen) {
            setChatsOpen(false);
            return;
          }
          closePanel();
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
            tellHost({ fitit: "resize-drag", width: size.width, height: size.height, x: event.screenX, y: event.screenY });
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
        <div className="panel-top">
        <header className="panel-header">
          <div className="title">
            <Mark />
            <h1>Fit-it</h1>
          </div>
          {signedOut ? null : (
            <div className="library-nav">
              <button
                type="button"
                className="library-button"
                id="features-trigger"
                ref={featuresTriggerRef}
                aria-expanded={sheet === "features"}
                aria-controls="features-sheet"
                aria-haspopup="dialog"
                onClick={() => toggleSheet("features")}
              >
                My features
              </button>
              <button
                type="button"
                className="library-button"
                id="marketplace-trigger"
                ref={marketTriggerRef}
                aria-expanded={sheet === "market"}
                aria-controls="marketplace-sheet"
                aria-haspopup="dialog"
                onClick={() => toggleSheet("market")}
              >
                Marketplace
              </button>
            </div>
          )}
          <div className="header-actions">
            <button type="button" className="icon-button" aria-label="Close Fit-it" onClick={closePanel}>
              <svg viewBox="0 0 16 16" aria-hidden="true">
                <path d="M3.5 6.5 L8 11 L12.5 6.5" />
              </svg>
            </button>
          </div>
        </header>
        {!signedOut && sheet ? (
          <>
            <div className="library-scrim" aria-hidden="true" onClick={() => closeSheet(false)} />
            <div
              className="library-sheet"
              id={sheetId}
              role="region"
              aria-labelledby={sheetHeadingId}
              tabIndex={-1}
              ref={sheetRef}
            >
              <div className="library-sheet-bar">
                <h2 id={sheetHeadingId}>{sheetTitle}</h2>
                {sheet === "features" && features.length > 0 ? (
                  <span className="count">
                    {enabledCount} of {features.length} on
                  </span>
                ) : null}
                <button
                  type="button"
                  className="icon-button"
                  aria-label={`Close ${sheetTitle}`}
                  onClick={() => closeSheet()}
                >
                  <svg viewBox="0 0 16 16" aria-hidden="true">
                    <path d="M4 4l8 8M12 4l-8 8" />
                  </svg>
                </button>
              </div>
              {sheet === "features" ? (
                <Features
                  features={features}
                  ready={featuresLoaded}
                  error={featuresError}
                  pending={pendingFeature}
                  onToggle={(feature) => {
                    if (feature.source === "own") {
                      void runFeature(featureKey(feature), () => toggleHook(feature.name), () => dropOwn(feature.name));
                    } else {
                      void runFeature(
                        featureKey(feature),
                        () => toggleSubscription(feature.feature_id),
                        () => dropSubscribed(feature.feature_id),
                      );
                    }
                  }}
                  onUndo={(name) => void runFeature(`own:${name}`, () => undoHook(name), () => dropOwn(name))}
                  onDelete={(feature) => {
                    if (feature.source === "own") {
                      void runFeature(featureKey(feature), () => deleteHook(feature.name), () => dropOwn(feature.name));
                    } else {
                      void runFeature(
                        featureKey(feature),
                        () => unsubscribeFeature(feature.feature_id),
                        () => dropSubscribed(feature.feature_id),
                      );
                    }
                  }}
                  onUpdate={(feature) =>
                    void runFeature(featureKey(feature), () => acceptUpdate(feature.feature_id), () =>
                      dropSubscribed(feature.feature_id),
                    )
                  }
                  onPublish={publish}
                  onUnpublish={(feature) => void unpublish(feature)}
                />
              ) : (
                <Marketplace onSignedOut={() => setSignedOut(true)} />
              )}
            </div>
          </>
        ) : null}
        </div>

        <div className="window-bar" ref={chatsRootRef}>
          <div className="window-tabs" role="tablist" aria-label="Chat windows" onKeyDown={onTabsKey}>
            {windows.map((chat) => {
              const label = chatLabel(chat);
              const selected = chat.key === active.key;
              return (
                <div
                  key={chat.key}
                  ref={selected ? activeTabRef : undefined}
                  className={selected ? "window-tab is-active" : "window-tab"}
                >
                  <button
                    type="button"
                    role="tab"
                    id={`tab-${chat.key}`}
                    className="window-tab-select"
                    aria-selected={selected}
                    aria-controls="chat-panel"
                    tabIndex={selected ? 0 : -1}
                    title={label}
                    aria-label={chat.busy ? `${label}, working` : label}
                    ref={(node) => {
                      if (node) tabRefs.current.set(chat.key, node);
                      else tabRefs.current.delete(chat.key);
                    }}
                    onClick={() => focusWindow(chat.key)}
                  >
                    {label}
                  </button>
                  {chat.busy && !selected ? <span className="window-busy" aria-hidden="true" /> : null}
                  {windows.length > 1 ? (
                    <button
                      type="button"
                      className="window-tab-close"
                      aria-label={`Close ${label}`}
                      onClick={() => closeWindow(chat.key)}
                    >
                      <svg viewBox="0 0 16 16" aria-hidden="true">
                        <path d="M4 4l8 8M12 4l-8 8" />
                      </svg>
                    </button>
                  ) : null}
                </div>
              );
            })}
          </div>
          <button
            type="button"
            className="window-add"
            aria-label="Open another chat"
            disabled={signedOut}
            onClick={() => showConversation(crypto.randomUUID())}
          >
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M8 3.5v9M3.5 8h9" />
            </svg>
          </button>
          <button
            type="button"
            className="chats-button"
            aria-label="Earlier chats"
            aria-expanded={chatsOpen}
            aria-haspopup="true"
            disabled={signedOut}
            onClick={() => {
              setSheet(null);
              setChatsOpen((current) => {
                if (!current) void refreshConversations();
                return !current;
              });
            }}
          >
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M3 4.5h10M3 8h10M3 11.5h6" />
            </svg>
          </button>
          {chatsOpen ? (
            <div className="conversation-popover" role="group" aria-label="Earlier chats">
              {conversations.length === 0 ? (
                <p className="conversation-empty">No earlier chats</p>
              ) : (
                <ul>
                  {conversations.map((conversation) => (
                    <li key={conversation.id}>
                      <button type="button" title={conversation.title} onClick={() => showConversation(conversation.id, conversation.title)}>
                        <span className="conversation-title">{conversation.title}</span>
                        {conversation.turns !== null ? <span className="conversation-meta">{conversation.turns}</span> : null}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : null}
        </div>

        <div
          className="transcript"
          id="chat-panel"
          role="tabpanel"
          aria-labelledby={`tab-${active.key}`}
          aria-live="polite"
          aria-relevant="additions"
        >
          {turns.length === 0 && !signedOut ? (
            <div className="welcome">
              <h2>What should this app do for you?</h2>
              <p>Describe a change in plain words. Only you will see it.</p>
              <div className="suggestions">
                {(suggestions ?? FALLBACK_SUGGESTIONS).map((text) => (
                  <button type="button" key={text} disabled={locked} onClick={() => pickSuggestion(text)}>
                    {text}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
          {turns.map((turn, turnIndex) => {
            const live = active.busy && turnIndex === turns.length - 1;
            return (
              <div className="turn" key={turnIndex}>
                {turn.prompt !== null ? <p className="user-message">{turn.prompt}</p> : null}
                {turn.replies.length > 0 || live ? (
                  <div className="reply">
                    <Mark />
                    <div className="reply-body">
                      {turn.replies.map((reply, index) => {
                        const last = index === turn.replies.length - 1;
                        switch (reply.kind) {
                          case "tools":
                            return (
                              <details className="activity" key={index} open={live && last ? true : undefined}>
                                <summary>
                                  <svg viewBox="0 0 16 16" aria-hidden="true">
                                    <path d="M6 4l4 4-4 4" />
                                  </svg>
                                  <span className={live && last ? "shimmer" : undefined}>
                                    {live && last
                                      ? `Working: ${reply.names[reply.names.length - 1]}`
                                      : `Ran ${reply.names.length} ${reply.names.length === 1 ? "step" : "steps"}`}
                                  </span>
                                </summary>
                                <ol>
                                  {reply.names.map((name, nameIndex) => (
                                    <li key={nameIndex}>
                                      <code>{name}</code>
                                    </li>
                                  ))}
                                </ol>
                              </details>
                            );
                          case "saved":
                            return (
                              <figure className="diff" key={index}>
                                <figcaption>
                                  <span>{reply.name}</span>
                                  <code>{reply.route}</code>
                                </figcaption>
                                <pre>
                                  {reply.diff.split("\n").map((line, lineIndex) => (
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
                              <p className="notice" key={index}>
                                {reply.name ? <strong>{reply.name} was rejected. </strong> : null}
                                {reply.reason}
                              </p>
                            );
                          case "answer":
                            return (
                              <p className="answer" key={index}>
                                {reply.text}
                              </p>
                            );
                          case "error":
                            return (
                              <p className="notice error" key={index}>
                                {reply.message}
                              </p>
                            );
                          case "changed":
                            return (
                              <p className="feature-changed" key={index}>
                                A feature changed.
                              </p>
                            );
                        }
                      })}
                      {live && turn.replies[turn.replies.length - 1]?.kind !== "tools" ? (
                        <p className="thinking">
                          <span className="thinking-dots" aria-hidden="true">
                            <span />
                            <span />
                            <span />
                          </span>
                          <span className="shimmer">Thinking…</span>
                        </p>
                      ) : null}
                    </div>
                  </div>
                ) : null}
              </div>
            );
          })}
          <div ref={bottomRef} />
        </div>

        <p className="status" role="status">
          {status}
        </p>

        <form
          className={dictatingHere ? "composer listening" : "composer"}
          onSubmit={(event) => {
            event.preventDefault();
            send();
          }}
          onClick={(event) => {
            if (event.target === event.currentTarget) composerRef.current?.focus();
          }}
        >
          <textarea
            ref={composerRef}
            aria-label="Describe a change to this app"
            placeholder={signedOut ? SIGN_IN : "Ask Fit-it to change this app…"}
            rows={1}
            value={active.draft}
            disabled={locked}
            onChange={(event) => {
              cancelRecording();
              const value = event.target.value;
              patch(active.key, (chat) => ({ ...chat, draft: value }));
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                send();
              }
            }}
          />
          {dictatingHere ? <Waveform /> : null}
          <div className="composer-bar">
            <span className={dictatingHere ? "hint listening" : "hint"}>
              {dictatingHere
                ? "Listening… click the mic to stop"
                : transcribingHere
                  ? "Transcribing…"
                  : "Enter to send, Shift + Enter for a new line"}
            </span>
            {micSupported ? (
              <button
                type="button"
                className={dictating ? "mic on" : "mic"}
                aria-label={dictating ? "Stop dictation" : "Dictate"}
                aria-pressed={dictating}
                disabled={signedOut || active.busy || transcribing}
                onClick={toggleDictation}
              >
                <svg viewBox="0 0 16 16" aria-hidden="true">
                  <rect x="5.5" y="1.75" width="5" height="8" rx="2.5" />
                  <path d="M3 7.5a5 5 0 0 0 10 0M8 12.5v2" />
                </svg>
              </button>
            ) : null}
            <button type="submit" className="send" aria-label="Send" disabled={locked || active.draft.trim() === ""}>
              {active.busy ? (
                <svg viewBox="0 0 16 16" aria-hidden="true" className="spin">
                  <path d="M8 2.5a5.5 5.5 0 1 1-5.5 5.5" />
                </svg>
              ) : (
                <svg viewBox="0 0 16 16" aria-hidden="true">
                  <path d="M8 13V3M3.5 7.5L8 3l4.5 4.5" />
                </svg>
              )}
            </button>
          </div>
        </form>
      </section>
    </main>
  );
}
