"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

type Ev = { seq: number; ts: number; kind: string; user: string | null; [k: string]: unknown };
type Flow = string[];
type Tab = "Overview" | "Flows" | "Agent" | "Events";

const CAP = 5000;
const WINDOWS: Record<string, number> = { "15 min": 900, "1 h": 3600, "24 h": 86400, All: Infinity };
const TABS: Tab[] = ["Overview", "Flows", "Agent", "Events"];
const STATUS = { "2xx": "#22a06b", "3xx": "#3b82f6", "4xx": "#e5a50a", "5xx": "#e5484d" } as const;
const C = { blue: "#3b82f6", violet: "#8b5cf6", teal: "#14b8a6", amber: "#e5a50a", red: "#e5484d", green: "#22a06b", slate: "#64748b" };

const s = (v: unknown) => (v == null ? "" : String(v));
const who = (e: Ev) => e.user ?? "anonymous";
const norm = (p: string) => p.split("?")[0].replace(/\/(\d+|[0-9a-f-]{8,}|[A-Z]{2,}-[\w-]*\d[\w-]*)(?=\/|$)/g, "/{id}");
const route = (e: Ev) => `${s(e.method)} ${norm(s(e.path))}`;
const STATIC_RE = /^\/(assets|files|private\/files|socket\.io)\/|\.(js|css|map|png|jpe?g|svg|ico|woff2?|ttf|gif|webp)$/i;
const isStatic = (e: Ev) => STATIC_RE.test(s(e.path).split("?")[0]);
// Route family: collapse ids, doctypes and method modules so ERPNext's hundreds of routes fit in a few groups.
function group(e: Ev) {
  const p = s(e.path).split("?")[0];
  if (STATIC_RE.test(p)) return "static assets";
  const parts = p.split("/").filter(Boolean);
  let g: string;
  if (parts[0] === "api" && parts[1] === "method") g = `/api/method/${(parts[2] ?? "").split(".").slice(0, 2).join(".")}.*`;
  else if (parts[0] === "api" && parts[1] === "resource") g = "/api/resource/{doctype}";
  else if (parts[0] === "app") g = parts.length > 1 ? "/app/{page}" : "/app";
  else g = norm(`/${parts.slice(0, 2).join("/")}`);
  return `${s(e.method)} ${g}`;
}
const hooksOf = (e: Ev) => (e.hooks as string[] | undefined) ?? [];
const statusClass = (st: number) => (st >= 500 ? "5xx" : st >= 400 ? "4xx" : st >= 300 ? "3xx" : "2xx");
const isFail = (e: Ev) => e.kind === "hook.failed" || e.kind === "hook.rejected" || Number(e.status) >= 500;
const fmt = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${(n / 1e3).toFixed(1)}k` : `${Math.round(n)}`);
const clip = (t: string, n: number) => (t.length > n ? `${t.slice(0, n - 1)}…` : t);

function pct(values: number[], p: number) {
  if (!values.length) return 0;
  const v = [...values].sort((a, b) => a - b);
  return v[Math.min(v.length - 1, Math.floor((p / 100) * v.length))];
}

function ago(ts: number, now: number) {
  const d = Math.max(0, Math.round(now - ts));
  return d < 60 ? `${d}s ago` : d < 3600 ? `${Math.floor(d / 60)}m ago` : d < 86400 ? `${Math.floor(d / 3600)}h ago` : `${Math.floor(d / 86400)}d ago`;
}

function family(kind: string) {
  return kind.startsWith("request") ? "req" : kind.startsWith("agent") ? "agent" : kind.startsWith("feature") ? "feat" : kind === "hook.failed" || kind === "hook.rejected" ? "bad" : "hook";
}

function summary(e: Ev): string {
  switch (e.kind) {
    case "request.proxied": {
      const hooks = (e.hooks as string[] | undefined) ?? [];
      return `${route(e)} → ${s(e.status)} · ${s(e.ms)} ms${hooks.length ? ` · ${hooks.join(", ")}` : ""}`;
    }
    case "hook.applied": return `${s(e.hook)} on ${s(e.route)} · ${s(e.ms)} ms`;
    case "hook.failed": return `${s(e.hook)} on ${s(e.route)}: ${s(e.error)}`;
    case "hook.rejected": return `${s(e.hook)}: ${s(e.reason)}`;
    case "agent.message": case "agent.answer": return s(e.text);
    case "agent.tool": return `${s(e.tool)}(${typeof e.input === "string" ? e.input : JSON.stringify(e.input ?? "")})`;
    default: return s(e.hook ?? e.feature_id ?? e.route);
  }
}

// Bucket events into ~40 time slots across the visible span.
function buckets(events: Ev[], from: number, to: number) {
  const step = Math.max(60, Math.ceil((to - from) / 40 / 60) * 60);
  const start = Math.floor(from / step) * step;
  const n = Math.max(1, Math.ceil((to - start) / step));
  const slots = Array.from({ length: n }, (_, i) => ({ t: start + i * step, ev: [] as Ev[] }));
  for (const e of events) {
    const i = Math.floor((e.ts - start) / step);
    if (i >= 0 && i < n) slots[i].ev.push(e);
  }
  return { step, slots };
}

function topN(counts: Map<string, number>, n: number) {
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, n).map(([name, value]) => ({ name, value }));
}

function count<T>(items: T[], key: (t: T) => string) {
  const m = new Map<string, number>();
  for (const i of items) m.set(key(i), (m.get(key(i)) ?? 0) + 1);
  return m;
}

/* ---------- Sankey (hand-written, column layout) ---------- */

const COLUMN_COLORS = [C.slate, C.blue, C.violet, C.teal];
const MAX_NODES = 10;

function Sankey({ flows, columns, outcomeColors }: { flows: Flow[]; columns: string[]; outcomeColors: Record<string, string> }) {
  const [hover, setHover] = useState<string | null>(null);
  const W = 1000, NW = 10, GAP = 10, MIN_H = 22, LABEL = 200;
  const layout = useMemo(() => {
    // Collapse the long tail of each column into "other" so labels stay readable.
    const clean = flows.map((f) => f.slice(0, columns.length).map((x) => String(x ?? "anonymous")));
    columns.forEach((_, c) => {
      const keep = new Set(topN(count(clean, (f) => f[c]), MAX_NODES - 1).map((x) => x.name));
      if (count(clean, (f) => f[c]).size > MAX_NODES) for (const f of clean) if (!keep.has(f[c])) f[c] = `other (${columns[c].toLowerCase()})`;
    });
    const links = new Map<string, number>();
    const nodes = columns.map(() => new Map<string, number>());
    for (const f of clean)
      f.forEach((name, c) => {
        nodes[c].set(name, (nodes[c].get(name) ?? 0) + 1);
        if (c < f.length - 1) {
          const k = `${c}\u0000${name}\u0000${f[c + 1]}`;
          links.set(k, (links.get(k) ?? 0) + 1);
        }
      });
    const total = clean.length || 1;
    const maxCount = Math.max(1, ...nodes.map((c) => c.size));
    const H = Math.max(260, maxCount * (MIN_H + GAP));
    const k = (H - GAP * maxCount) / total;
    const inner = W - LABEL * 2;
    const pos = nodes.map((col, c) => {
      let y = 0;
      const x = LABEL + (c * (inner - NW)) / Math.max(1, columns.length - 1);
      return new Map([...col.entries()].sort((a, b) => b[1] - a[1]).map(([name, v]) => {
        const h = Math.max(MIN_H, v * k);
        const p = { x, y, h, v, outY: y, inY: y };
        y += h + GAP;
        return [name, p];
      }));
    });
    const height = Math.max(...pos.map((col) => Math.max(0, ...[...col.values()].map((p) => p.y + p.h))));
    const paths = [...links.entries()].sort((a, b) => b[1] - a[1]).map(([key, v]) => {
      const [c, from, to] = key.split("\u0000");
      const a = pos[+c].get(from)!, b = pos[+c + 1].get(to)!;
      const w = Math.max(1.5, v * k), y0 = a.outY + w / 2, y1 = b.inY + w / 2;
      a.outY += w;
      b.inY += w;
      const x0 = a.x + NW, x1 = b.x, mx = (x0 + x1) / 2;
      const last = +c + 1 === columns.length - 1;
      return { key, from, to, v, w, d: `M${x0},${y0}C${mx},${y0} ${mx},${y1} ${x1},${y1}`, color: last ? outcomeColors[to] ?? COLUMN_COLORS[+c + 1] : COLUMN_COLORS[+c + 1] };
    });
    return { pos, paths, height };
  }, [flows, columns, outcomeColors]);

  if (!flows.length) return <p className="empty">No flows in this time window yet.</p>;
  const lastCol = columns.length - 1;
  return (
    <div className="sankey">
      <svg viewBox={`0 -28 ${W} ${layout.height + 36}`} role="img" aria-label="Flow diagram">
        {columns.map((c, i) => {
          const x = layout.pos[i].values().next().value?.x ?? 0;
          return <text key={c} className="col" x={i === lastCol ? x + NW : x} y={-12} textAnchor={i === lastCol ? "end" : "start"}>{c}</text>;
        })}
        {layout.paths.map((p) => (
          <path key={p.key} d={p.d} stroke={p.color} strokeWidth={p.w} fill="none"
            opacity={hover ? (hover === p.from || hover === p.to ? 0.55 : 0.06) : 0.28}>
            <title>{`${p.from} → ${p.to}: ${p.v}`}</title>
          </path>
        ))}
        {layout.pos.map((col, c) => [...col.entries()].map(([name, n]) => {
          const right = c === lastCol;
          const fill = c === lastCol ? outcomeColors[name] ?? COLUMN_COLORS[c] : COLUMN_COLORS[c];
          return (
            <g key={`${c}${name}`} onMouseEnter={() => setHover(name)} onMouseLeave={() => setHover(null)} className="node">
              <rect x={n.x} y={n.y} width={NW} height={n.h} rx={2} fill={fill}><title>{`${name}: ${n.v}`}</title></rect>
              <text x={right ? n.x + NW + 8 : c === 0 ? n.x - 8 : n.x + NW + 8} y={n.y + n.h / 2} dy="0.35em" textAnchor={c === 0 ? "end" : "start"}>
                <tspan>{clip(name, c === 0 || right ? 26 : 30)}</tspan>
                <tspan className="num" dx="6">{n.v}</tspan>
              </text>
            </g>
          );
        }))}
      </svg>
      <div className="legend">
        {columns.map((c, i) => i < lastCol && <span key={c}><i style={{ background: COLUMN_COLORS[i] }} />{c}</span>)}
        {Object.entries(outcomeColors).map(([k, v]) => <span key={k}><i style={{ background: v }} />{k}</span>)}
      </div>
    </div>
  );
}

/* ---------- small building blocks ---------- */

function Kpi({ label, value, unit, trend, spark, color = C.blue, bad }: {
  label: string; value: string; unit?: string; trend?: number | null; spark: number[]; color?: string; bad?: boolean;
}) {
  const up = (trend ?? 0) > 0;
  return (
    <div className="kpi">
      <span className="kpi-label">{label}</span>
      <div className="kpi-row">
        <b className={bad ? "bad" : ""}>{value}<small>{unit}</small></b>
        {trend != null && Number.isFinite(trend) && (
          <span className={`trend ${trend === 0 ? "" : up ? "up" : "down"}`}>{up ? "▲" : trend < 0 ? "▼" : "•"} {Math.abs(Math.round(trend))}%</span>
        )}
      </div>
      <div className="spark">
        <ResponsiveContainer width="100%" height={32}>
          <AreaChart data={spark.map((v, i) => ({ i, v }))} margin={{ top: 2, bottom: 0, left: 0, right: 0 }}>
            <Area dataKey="v" stroke={color} fill={color} fillOpacity={0.15} strokeWidth={1.5} isAnimationActive={false} dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function Panel({ title, sub, children, wide }: { title: string; sub?: string; children: React.ReactNode; wide?: boolean }) {
  return (
    <section className={`tcard${wide ? " wide" : ""}`}>
      <header><h2>{title}</h2>{sub && <span>{sub}</span>}</header>
      {children}
    </section>
  );
}

const axis = { stroke: "var(--muted)", fontSize: 11, tickLine: false, axisLine: false } as const;
const tip = { contentStyle: { background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 8, fontSize: 12, color: "var(--ink)" }, cursor: { fill: "var(--hover)" } };

function HBar({ data, color, unit = "" }: { data: { name: string; value: number }[]; color: string; unit?: string }) {
  if (!data.length) return <p className="empty">No data yet.</p>;
  return (
    <ResponsiveContainer width="100%" height={Math.max(120, data.length * 30 + 20)}>
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 24, top: 4, bottom: 4 }}>
        <CartesianGrid horizontal={false} stroke="var(--line)" />
        <XAxis type="number" {...axis} />
        <YAxis type="category" dataKey="name" width={190} {...axis} tick={{ fill: "var(--ink-soft)", fontSize: 11 }} tickFormatter={(v: string) => clip(v, 30)} />
        <Tooltip {...tip} formatter={(v: number) => [`${fmt(v)}${unit}`, ""]} separator="" />
        <Bar dataKey="value" fill={color} radius={[0, 4, 4, 0]} barSize={16} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

type Stats = { n: number; p50: number; p95: number; avg: number };
function stats(evs: Ev[]): Stats {
  const ms = evs.map((e) => Number(e.ms) || 0);
  return { n: ms.length, p50: pct(ms, 50), p95: pct(ms, 95), avg: ms.length ? ms.reduce((a, b) => a + b, 0) / ms.length : 0 };
}

function PerfTile({ title, st, color }: { title: string; st: Stats; color: string }) {
  return (
    <div className="kpi perf-tile" style={{ borderTop: `3px solid ${color}` }}>
      <span className="kpi-label">{title}</span>
      <div className="perf-nums">
        {([["requests", fmt(st.n), ""], ["p50", fmt(st.p50), "ms"], ["p95", fmt(st.p95), "ms"], ["avg", fmt(st.avg), "ms"]] as const).map(([k, v, u]) => (
          <div key={k}><b>{st.n ? v : "–"}<small>{st.n && u}</small></b><span>{k}</span></div>
        ))}
      </div>
    </div>
  );
}

/* ---------- page ---------- */

export default function Traceability() {
  const [events, setEvents] = useState<Ev[]>([]);
  const [admin, setAdmin] = useState(false);
  const [signedOut, setSignedOut] = useState(false);
  const [user, setUser] = useState("");
  const [win, setWin] = useState("1 h");
  const [tab, setTab] = useState<Tab>("Overview");
  const [kind, setKind] = useState("");
  const [withStatic, setWithStatic] = useState(false);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now() / 1000);

  useEffect(() => {
    let after = 0, stop = false, timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const r = await fetch(`/fitit/trace/events?after=${after}&limit=1000`, { credentials: "same-origin" });
        if (r.status === 401) setSignedOut(true);
        else if (r.ok) {
          const body: { events: Ev[]; next: number; admin: boolean } = await r.json();
          after = body.next ?? after;
          setAdmin(body.admin);
          setSignedOut(false);
          if (body.events.length) setEvents((prev) => prev.concat(body.events).slice(-CAP));
        }
      } catch {
        // network blip: retry on the next tick
      }
      setNow(Date.now() / 1000);
      if (!stop) timer = setTimeout(poll, 3000);
    };
    poll();
    return () => { stop = true; clearTimeout(timer); };
  }, []);

  const users = useMemo(() => [...new Set(events.map(who))].sort(), [events]);
  const kinds = useMemo(() => [...new Set(events.map((e) => e.kind))].sort(), [events]);

  const d = useMemo(() => {
    const first = events[0]?.ts ?? now;
    const from = Math.max(first, now - WINDOWS[win]);
    const shown = events.filter((e) => e.ts >= from && (!user || who(e) === user));
    const req = shown.filter((e) => e.kind === "request.proxied" && (withStatic || !isStatic(e)));
    const groupCounts = count(req, group);
    const topGroups = new Set(topN(groupCounts, 8).map((x) => x.name));
    const grp = (e: Ev) => (topGroups.has(group(e)) ? group(e) : "Other");
    const byGroup = [...topGroups, ...(groupCounts.size > 8 ? ["Other"] : [])].map((name) => {
      const row: Record<string, number | string> = { name };
      for (const k of Object.keys(STATUS)) row[k] = req.filter((e) => grp(e) === name && statusClass(Number(e.status)) === k).length;
      return row;
    });
    const showUser = !user && new Set(req.map(who)).size <= 6;
    const hooked = req.filter((e) => hooksOf(e).length);
    const applied = shown.filter((e) => e.kind === "hook.applied");
    const failed = shown.filter((e) => e.kind === "hook.failed");
    const answers = shown.filter((e) => e.kind === "agent.answer");
    const tools = shown.filter((e) => e.kind === "agent.tool");
    const { step, slots } = buckets(shown, from, now);
    const label = (t: number) => new Date(t * 1000).toLocaleTimeString([], step >= 86400 ? { month: "short", day: "numeric" } : { hour: "2-digit", minute: "2-digit" });
    const series = slots.map(({ t, ev }) => {
      const r = ev.filter((e) => e.kind === "request.proxied");
      const ms = r.map((e) => Number(e.ms) || 0);
      const row: Record<string, number | string> = { t: label(t), requests: r.length, errors: r.filter((e) => Number(e.status) >= 500).length,
        p50: pct(ms, 50), p95: pct(ms, 95), applied: ev.filter((e) => e.kind === "hook.applied").length,
        failed: ev.filter((e) => e.kind === "hook.failed").length, messages: ev.filter((e) => e.kind === "agent.message").length,
        users: new Set(ev.map(who)).size, tokensIn: 0, tokensOut: 0 };
      for (const k of Object.keys(STATUS)) row[k] = r.filter((e) => statusClass(Number(e.status)) === k).length;
      for (const e of ev) if (e.kind === "agent.answer") { (row.tokensIn as number) += Number(e.input_tokens) || 0; (row.tokensOut as number) += Number(e.output_tokens) || 0; }
      return row;
    });
    const ms = req.map((e) => Number(e.ms) || 0);
    const hookMs = new Map<string, number[]>();
    for (const e of applied) hookMs.set(s(e.hook), [...(hookMs.get(s(e.hook)) ?? []), Number(e.ms) || 0]);
    const plain = req.filter((e) => !hooksOf(e).length);
    const perfGroups = topN(groupCounts, 8).map(({ name }) => {
      const g = req.filter((e) => group(e) === name);
      const o = stats(g.filter((e) => !hooksOf(e).length)), f = stats(g.filter((e) => hooksOf(e).length));
      return { name, Original: o.n ? Math.round(o.avg) : null, "With Fit-it": f.n ? Math.round(f.avg) : null };
    });
    const lifecycle = shown.filter((e) => ["hook.saved", "hook.rejected", "hook.restored", "hook.toggled", "hook.deleted"].includes(e.kind) || e.kind.startsWith("feature."));
    return {
      shown, req, applied, failed, series, step, lifecycle, tools,
      errorRate: req.length ? (req.filter((e) => Number(e.status) >= 500).length / req.length) * 100 : 0,
      p50: pct(ms, 50), p95: pct(ms, 95),
      tokensIn: answers.reduce((a, e) => a + (Number(e.input_tokens) || 0), 0),
      tokensOut: answers.reduce((a, e) => a + (Number(e.output_tokens) || 0), 0),
      messages: shown.filter((e) => e.kind === "agent.message").length,
      activeUsers: new Set(shown.map(who)).size,
      topRoutes: topN(groupCounts, 8),
      slowHooks: [...hookMs.entries()].map(([name, v]) => ({ name, value: Math.round(v.reduce((a, b) => a + b, 0) / v.length) })).sort((a, b) => b.value - a.value).slice(0, 8),
      topHooks: topN(count(applied, (e) => s(e.hook)), 8),
      toolUse: topN(count(tools, (e) => s(e.tool)), 8),
      byGroup, showUser, hookedCount: hooked.length,
      orig: stats(plain), fit: stats(hooked), perfGroups,
      reqFlows: hooked.flatMap((e) => hooksOf(e).map((h) => [...(showUser ? [who(e)] : []), grp(e), h, statusClass(Number(e.status))]))
        .concat(failed.map((e) => [...(showUser ? [who(e)] : []), s(e.route), s(e.hook), "failed"])),
      agentFlows: shown.filter((e) => ["agent.tool", "hook.saved", "hook.rejected"].includes(e.kind))
        .map((e) => (e.kind === "agent.tool" ? [who(e), s(e.tool), "called"] : [who(e), "write hook", e.kind === "hook.saved" ? "saved" : "rejected"])),
    };
  }, [events, win, user, now, withStatic]);

  // Trend: second half of the window against the first half.
  const trend = (key: string) => {
    const v = d.series.map((r) => Number(r[key]) || 0);
    const h = Math.floor(v.length / 2);
    const a = v.slice(0, h).reduce((x, y) => x + y, 0), b = v.slice(h).reduce((x, y) => x + y, 0);
    return a ? ((b - a) / a) * 100 : null;
  };
  const spark = (key: string) => d.series.map((r) => Number(r[key]) || 0);
  const per = d.step >= 3600 ? `${d.step / 3600} h` : `${d.step / 60} min`;

  const rows = useMemo(() => {
    const needle = q.toLowerCase();
    return d.shown.filter((e) => (!kind || e.kind === kind) && (!needle || `${who(e)} ${e.kind} ${summary(e)}`.toLowerCase().includes(needle))).slice(-300).reverse();
  }, [d.shown, kind, q]);

  const recentAgent = d.shown.filter((e) => e.kind === "agent.message" || e.kind === "agent.answer").slice(-12).reverse();

  return (
    <div className="trace">
      <style>{CSS}</style>
      <header className="top">
        <div className="brand">
          <h1>Traceability</h1>
          <span className="live"><i />Live · {events.length.toLocaleString()} events</span>
        </div>
        <div className="controls">
          {admin && (
            <select value={user} onChange={(e) => setUser(e.target.value)} aria-label="User">
              <option value="">All users</option>
              {users.map((u) => <option key={u}>{u}</option>)}
            </select>
          )}
          <label className="check"><input type="checkbox" checked={withStatic} onChange={(e) => setWithStatic(e.target.checked)} /> Static assets</label>
          <div className="seg" role="group" aria-label="Time window">
            {Object.keys(WINDOWS).map((w) => <button key={w} aria-pressed={win === w} onClick={() => setWin(w)}>{w}</button>)}
          </div>
        </div>
      </header>
      <nav className="tabs">
        {TABS.map((t) => <button key={t} aria-selected={tab === t} onClick={() => setTab(t)}>{t}</button>)}
      </nav>

      {signedOut ? (
        <p className="empty big">Log in to the app to see traceability.</p>
      ) : (
        <main>
          {tab === "Overview" && (
            <>
              <section className="tcard perf">
                <header><h2>Performance: original app vs Fit-it</h2></header>
                <p className="muted perf-sub">Original = requests with no hook; Fit-it = requests where at least one hook or feature ran. Hook overhead = time inside the hooks.</p>
                <div className="perf-tiles">
                  <PerfTile title="Original app (no hooks)" st={d.orig} color={C.slate} />
                  <PerfTile title="With Fit-it (hooks / features)" st={d.fit} color={C.violet} />
                  {d.orig.n > 0 && d.fit.n > 0 && (() => {
                    const delta = d.fit.avg - d.orig.avg, rel = d.orig.avg ? (delta / d.orig.avg) * 100 : 0;
                    return <span className={`delta ${delta > 0 ? "up" : "down"}`} title="Average latency, Fit-it vs original">{delta >= 0 ? "+" : "−"}{fmt(Math.abs(delta))} ms, {rel >= 0 ? "+" : "−"}{Math.abs(Math.round(rel))}%</span>;
                  })()}
                </div>
                <div className="grid">
                  <div>
                    <h3>Average ms by route group</h3>
                    {d.perfGroups.length ? (
                      <ResponsiveContainer width="100%" height={Math.max(160, d.perfGroups.length * 40 + 40)}>
                        <BarChart data={d.perfGroups} layout="vertical" margin={{ left: 8, right: 24, top: 4, bottom: 4 }}>
                          <CartesianGrid horizontal={false} stroke="var(--line)" />
                          <XAxis type="number" {...axis} unit=" ms" />
                          <YAxis type="category" dataKey="name" width={190} {...axis} tick={{ fill: "var(--ink-soft)", fontSize: 11 }} tickFormatter={(v: string) => clip(v, 30)} />
                          <Tooltip {...tip} formatter={(v: number) => `${fmt(v)} ms`} />
                          <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
                          <Bar dataKey="Original" fill={C.slate} radius={[0, 4, 4, 0]} barSize={10} isAnimationActive={false} />
                          <Bar dataKey="With Fit-it" fill={C.violet} radius={[0, 4, 4, 0]} barSize={10} isAnimationActive={false} />
                        </BarChart>
                      </ResponsiveContainer>
                    ) : <p className="empty">No requests in this window yet.</p>}
                  </div>
                  <div>
                    <h3>Hook overhead <span className="muted">average ms inside each hook / feature</span></h3>
                    <HBar data={d.slowHooks} color={C.amber} unit=" ms" />
                  </div>
                </div>
              </section>
              <div className="kpis">
                <Kpi label="Requests" value={fmt(d.req.length)} trend={trend("requests")} spark={spark("requests")} />
                <Kpi label="Error rate" value={d.errorRate.toFixed(1)} unit="%" trend={trend("errors")} spark={spark("errors")} color={C.red} bad={d.errorRate > 5} />
                <Kpi label="Latency p50 / p95" value={`${fmt(d.p50)} / ${fmt(d.p95)}`} unit="ms" trend={trend("p95")} spark={spark("p95")} color={C.amber} />
                <Kpi label="Hooks applied" value={fmt(d.applied.length)} trend={trend("applied")} spark={spark("applied")} color={C.violet} />
                <Kpi label="Hook failures" value={fmt(d.failed.length)} trend={trend("failed")} spark={spark("failed")} color={C.red} bad={d.failed.length > 0} />
                <Kpi label="Agent messages" value={fmt(d.messages)} trend={trend("messages")} spark={spark("messages")} color={C.teal} />
                <Kpi label="Tokens in / out" value={`${fmt(d.tokensIn)} / ${fmt(d.tokensOut)}`} trend={trend("tokensIn")} spark={spark("tokensIn")} color={C.green} />
                <Kpi label="Active users" value={fmt(d.activeUsers)} trend={trend("users")} spark={spark("users")} color={C.slate} />
              </div>
              <div className="grid">
                <Panel title="Requests" sub={`per ${per}, by status`} wide>
                  <ResponsiveContainer width="100%" height={240}>
                    <BarChart data={d.series} margin={{ left: -16, right: 8, top: 8 }}>
                      <CartesianGrid vertical={false} stroke="var(--line)" />
                      <XAxis dataKey="t" {...axis} minTickGap={24} />
                      <YAxis {...axis} allowDecimals={false} />
                      <Tooltip {...tip} />
                      <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
                      {Object.entries(STATUS).map(([k, c]) => <Bar key={k} dataKey={k} stackId="s" fill={c} isAnimationActive={false} />)}
                    </BarChart>
                  </ResponsiveContainer>
                </Panel>
                <Panel title="Latency" sub="ms, p50 and p95">
                  <ResponsiveContainer width="100%" height={220}>
                    <LineChart data={d.series} margin={{ left: -16, right: 8, top: 8 }}>
                      <CartesianGrid vertical={false} stroke="var(--line)" />
                      <XAxis dataKey="t" {...axis} minTickGap={24} />
                      <YAxis {...axis} />
                      <Tooltip {...tip} />
                      <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
                      <Line dataKey="p50" stroke={C.blue} dot={false} strokeWidth={2} isAnimationActive={false} />
                      <Line dataKey="p95" stroke={C.amber} dot={false} strokeWidth={2} isAnimationActive={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </Panel>
                <Panel title="Hook activity" sub={`per ${per}`}>
                  <ResponsiveContainer width="100%" height={220}>
                    <AreaChart data={d.series} margin={{ left: -16, right: 8, top: 8 }}>
                      <CartesianGrid vertical={false} stroke="var(--line)" />
                      <XAxis dataKey="t" {...axis} minTickGap={24} />
                      <YAxis {...axis} allowDecimals={false} />
                      <Tooltip {...tip} />
                      <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
                      <Area dataKey="applied" stroke={C.violet} fill={C.violet} fillOpacity={0.15} isAnimationActive={false} />
                      <Area dataKey="failed" stroke={C.red} fill={C.red} fillOpacity={0.15} isAnimationActive={false} />
                    </AreaChart>
                  </ResponsiveContainer>
                </Panel>
                <Panel title="Top routes" sub="by requests"><HBar data={d.topRoutes} color={C.blue} /></Panel>
                <Panel title="Top hooks applied" sub="by executions"><HBar data={d.topHooks} color={C.violet} /></Panel>
                <Panel title="Slowest hooks" sub="average ms"><HBar data={d.slowHooks} color={C.amber} unit=" ms" /></Panel>
                <Panel title="Hook failures" sub="latest">
                  {d.failed.length ? (
                    <ul className="list">{d.failed.slice(-6).reverse().map((e) => (
                      <li key={e.seq}><b className="bad">{s(e.hook)}</b><span>{s(e.route)}</span><em>{clip(s(e.error), 120)}</em><time>{ago(e.ts, now)}</time></li>
                    ))}</ul>
                  ) : <p className="empty ok">No hook failures in this window.</p>}
                </Panel>
              </div>
            </>
          )}

          {tab === "Flows" && (
            <div className="grid">
              <Panel title="Requests by route group" sub={`top 8 groups · ${fmt(d.req.length)} requests${withStatic ? "" : " · static assets excluded"}`} wide>
                <ResponsiveContainer width="100%" height={Math.max(160, d.byGroup.length * 34 + 40)}>
                  <BarChart data={d.byGroup} layout="vertical" margin={{ left: 8, right: 24, top: 4, bottom: 4 }}>
                    <CartesianGrid horizontal={false} stroke="var(--line)" />
                    <XAxis type="number" {...axis} allowDecimals={false} />
                    <YAxis type="category" dataKey="name" width={240} {...axis} tick={{ fill: "var(--ink-soft)", fontSize: 11 }} tickFormatter={(v: string) => clip(v, 38)} />
                    <Tooltip {...tip} />
                    <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
                    {Object.entries(STATUS).map(([k, c], i, all) => (
                      <Bar key={k} dataKey={k} stackId="s" fill={c} barSize={18} isAnimationActive={false} radius={i === all.length - 1 ? [0, 4, 4, 0] : 0} />
                    ))}
                  </BarChart>
                </ResponsiveContainer>
              </Panel>
              <Panel title="Hooked requests" sub={`${d.showUser ? "user → " : ""}route group → hook / feature → outcome · ${fmt(d.hookedCount)} requests where a hook ran`} wide>
                <Sankey flows={d.reqFlows} columns={[...(d.showUser ? ["User"] : []), "Route group", "Hook / feature", "Outcome"]}
                  outcomeColors={{ ...STATUS, failed: C.red }} />
              </Panel>
              <Panel title="Agent flow" sub="user → tool → outcome" wide>
                <Sankey flows={d.agentFlows} columns={["User", "Tool", "Outcome"]} outcomeColors={{ called: C.blue, saved: C.green, rejected: C.red }} />
              </Panel>
            </div>
          )}

          {tab === "Agent" && (
            <div className="grid">
              <Panel title="Tokens" sub={`per ${per}`} wide>
                <ResponsiveContainer width="100%" height={220}>
                  <AreaChart data={d.series} margin={{ left: -8, right: 8, top: 8 }}>
                    <CartesianGrid vertical={false} stroke="var(--line)" />
                    <XAxis dataKey="t" {...axis} minTickGap={24} />
                    <YAxis {...axis} tickFormatter={fmt} />
                    <Tooltip {...tip} />
                    <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
                    <Area dataKey="tokensIn" name="input" stackId="t" stroke={C.teal} fill={C.teal} fillOpacity={0.2} isAnimationActive={false} />
                    <Area dataKey="tokensOut" name="output" stackId="t" stroke={C.violet} fill={C.violet} fillOpacity={0.2} isAnimationActive={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </Panel>
              <Panel title="Tool usage" sub="calls"><HBar data={d.toolUse} color={C.teal} /></Panel>
              <Panel title="Hook lifecycle" sub="saved · rejected · restored · toggled · deleted">
                <div className="chips">
                  {["hook.saved", "hook.rejected", "hook.restored", "hook.toggled", "hook.deleted"].map((k) => (
                    <span key={k} className={`badge ${family(k)}`}>{k.slice(5)} <b>{d.lifecycle.filter((e) => e.kind === k).length}</b></span>
                  ))}
                </div>
                {d.lifecycle.length ? (
                  <ul className="list">{d.lifecycle.slice(-10).reverse().map((e) => (
                    <li key={e.seq}>
                      <span className={`badge ${family(e.kind)}`}>{e.kind.split(".")[1]}</span>
                      <b className={s(e.hook).startsWith("@") ? "feat" : ""}>{s(e.hook ?? e.feature_id)}</b>
                      <em>{clip(s(e.reason ?? e.route ?? who(e)), 120)}</em>
                      <time>{ago(e.ts, now)}</time>
                    </li>
                  ))}</ul>
                ) : <p className="empty">No hook changes in this window.</p>}
              </Panel>
              <Panel title="Recent conversation" sub="agent messages and answers" wide>
                {recentAgent.length ? (
                  <ul className="chat">{recentAgent.map((e) => (
                    <li key={e.seq} className={e.kind === "agent.answer" ? "ans" : "msg"}>
                      <header><b>{e.kind === "agent.answer" ? "Agent" : who(e)}</b><time>{ago(e.ts, now)}</time>
                        {e.kind === "agent.answer" && <span className="tok">{fmt(Number(e.input_tokens) || 0)} in · {fmt(Number(e.output_tokens) || 0)} out</span>}</header>
                      <p>{clip(s(e.text), 400)}</p>
                    </li>
                  ))}</ul>
                ) : <p className="empty">No agent conversations in this window.</p>}
              </Panel>
            </div>
          )}

          {tab === "Events" && (
            <section className="tcard wide">
              <header className="evbar">
                <input type="search" placeholder="Search user, kind, route, hook…" value={q} onChange={(e) => setQ(e.target.value)} />
                <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind">
                  <option value="">All kinds</option>
                  {kinds.map((k) => <option key={k}>{k}</option>)}
                </select>
                <span className="muted">{rows.length} shown</span>
              </header>
              <div className="scroll">
                <table>
                  <thead><tr><th>When</th><th>User</th><th>Kind</th><th>Details</th></tr></thead>
                  <tbody>
                    {rows.map((e) => (
                      <Fragment key={e.seq}>
                        <tr className={isFail(e) ? "bad" : ""} onClick={() => setOpen(open === e.seq ? null : e.seq)}>
                          <td title={new Date(e.ts * 1000).toLocaleString()}>{ago(e.ts, now)}</td>
                          <td>{who(e)}</td>
                          <td><span className={`badge ${family(e.kind)}`}>{e.kind}</span></td>
                          <td className="sum"><span className="caret">{open === e.seq ? "▾" : "▸"}</span>{clip(summary(e), 220)}</td>
                        </tr>
                        {open === e.seq && (
                          <tr className="detail"><td colSpan={4}>
                            {e.kind === "hook.saved" && s(e.diff) ? (
                              <pre>{s(e.diff).split("\n").map((l, i) => <div key={i} className={l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : ""}>{l}</div>)}</pre>
                            ) : <pre>{JSON.stringify(e, null, 2)}</pre>}
                          </td></tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
                {!rows.length && <p className="empty">No events match these filters.</p>}
              </div>
            </section>
          )}
        </main>
      )}
    </div>
  );
}

const CSS = `
html, body { background: var(--bg, #f7f7f8); height: auto; }
.trace { --bg:#f7f7f8; --panel:#ffffff; --ink:#111318; --ink-soft:#3f4450; --muted:#6b7280; --line:#e6e7ea; --hover:rgba(0,0,0,.04);
  --accent:#3b82f6; --bad:#d93036; --good:#1a8a5a;
  color: var(--ink); background: var(--bg); min-height: 100vh; font-family: var(--sans); font-size: 13px; }
@media (prefers-color-scheme: dark) {
  html, body { background:#0e0f12; }
  .trace { --bg:#0e0f12; --panel:#16181d; --ink:#eceef2; --ink-soft:#c3c7cf; --muted:#8b919c; --line:#262a31; --hover:rgba(255,255,255,.05);
    --accent:#60a5fa; --bad:#ff6b6b; --good:#3ecf8e; color-scheme: dark; }
}
.trace .top { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; padding:20px 24px 12px; max-width:1440px; margin:0 auto; }
.trace .brand { display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; }
.trace h1 { font-size:20px; font-weight:600; margin:0; letter-spacing:-0.01em; }
.trace .live { display:inline-flex; align-items:center; gap:6px; color:var(--muted); font-size:12px; }
.trace .live i { width:7px; height:7px; border-radius:50%; background:var(--good); box-shadow:0 0 0 3px color-mix(in srgb, var(--good) 25%, transparent); }
.trace .controls { display:flex; gap:8px; flex-wrap:wrap; }
.trace select, .trace input[type=search] { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:6px 10px; color:var(--ink); height:32px; }
.trace .check { display:inline-flex; align-items:center; gap:6px; color:var(--muted); font-size:12px; }
.trace .seg { display:inline-flex; background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:2px; }
.trace .seg button { border:0; background:none; padding:4px 10px; border-radius:6px; color:var(--muted); font-size:12px; }
.trace .seg button[aria-pressed=true] { background:var(--hover); color:var(--ink); font-weight:600; }
.trace .tabs { display:flex; gap:4px; padding:0 24px; max-width:1440px; margin:0 auto; border-bottom:1px solid var(--line); overflow-x:auto; }
.trace .tabs button { border:0; background:none; padding:10px 12px; color:var(--muted); font-weight:500; border-bottom:2px solid transparent; margin-bottom:-1px; }
.trace .tabs button[aria-selected=true] { color:var(--ink); border-color:var(--accent); }
.trace main { max-width:1440px; margin:0 auto; padding:20px 24px 48px; }
.trace .kpis { display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:12px; margin-bottom:16px; }
.trace .kpi { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px 14px 6px; min-width:0; }
.trace .kpi-label { color:var(--muted); font-size:12px; }
.trace .kpi-row { display:flex; align-items:baseline; justify-content:space-between; gap:8px; margin-top:2px; }
.trace .kpi b { font-size:22px; font-weight:600; font-variant-numeric:tabular-nums; white-space:nowrap; letter-spacing:-0.01em; }
.trace .kpi b small { font-size:12px; color:var(--muted); font-weight:500; margin-left:3px; }
.trace .trend { font-size:11px; color:var(--muted); white-space:nowrap; }
.trace .trend.up { color:var(--accent); } .trace .trend.down { color:var(--muted); }
.trace .spark { margin:4px -4px 0; }
.trace .grid { display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:12px; }
.trace .tcard { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px 16px; min-width:0; }
.trace .tcard.wide { grid-column:1 / -1; }
.trace .tcard > header { display:flex; align-items:baseline; gap:8px; margin-bottom:10px; flex-wrap:wrap; }
.trace .tcard h2 { font-size:14px; font-weight:600; margin:0; }
.trace .tcard > header span, .trace .muted { color:var(--muted); font-size:12px; }
.trace .bad { color:var(--bad) !important; }
.trace .feat { color:#8b5cf6; }
.trace .empty { color:var(--muted); text-align:center; padding:28px 8px; margin:0; }
.trace .empty.ok { color:var(--good); }
.trace .empty.big { font-size:15px; padding:80px 16px; }
.trace .sankey { overflow-x:auto; }
.trace .sankey svg { width:100%; min-width:760px; height:auto; display:block; }
.trace .sankey text { font-size:12px; fill:var(--ink-soft); paint-order:stroke; stroke:var(--panel); stroke-width:3px; stroke-linejoin:round; }
.trace .sankey text.col { fill:var(--muted); font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:.06em; }
.trace .sankey .num { fill:var(--muted); font-variant-numeric:tabular-nums; }
.trace .sankey path { transition:opacity .15s; }
.trace .sankey .node { cursor:default; }
.trace .legend { display:flex; flex-wrap:wrap; gap:14px; margin-top:10px; color:var(--muted); font-size:12px; }
.trace .legend span { display:inline-flex; align-items:center; gap:6px; }
.trace .legend i { width:10px; height:10px; border-radius:2px; display:inline-block; }
.trace .badge { display:inline-flex; align-items:center; gap:4px; font-family:var(--mono); font-size:11px; padding:1px 7px; border-radius:999px; white-space:nowrap;
  background:color-mix(in srgb, var(--c) 14%, transparent); color:var(--c); --c:var(--muted); }
.trace .badge.req { --c:#3b82f6; } .trace .badge.hook { --c:#8b5cf6; } .trace .badge.agent { --c:#14b8a6; } .trace .badge.feat { --c:#e5a50a; } .trace .badge.bad { --c:var(--bad); }
.trace .chips { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:10px; }
.trace .list { list-style:none; margin:0; padding:0; }
.trace .list li { display:grid; grid-template-columns:auto auto 1fr auto; gap:8px; align-items:baseline; padding:7px 0; border-top:1px solid var(--line); }
.trace .list li:first-child { border-top:0; }
.trace .list em { font-style:normal; color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.trace .list time, .trace .chat time { color:var(--muted); font-size:11px; white-space:nowrap; }
.trace .chat { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:8px; }
.trace .chat li { border:1px solid var(--line); border-radius:8px; padding:8px 12px; }
.trace .chat li.ans { background:var(--hover); }
.trace .chat header { display:flex; gap:8px; align-items:baseline; }
.trace .chat .tok { margin-left:auto; color:var(--muted); font-size:11px; font-family:var(--mono); }
.trace .chat p { margin:4px 0 0; color:var(--ink-soft); white-space:pre-wrap; word-break:break-word; }
.trace .evbar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.trace .evbar input { flex:1; min-width:200px; }
.trace .scroll { overflow-x:auto; }
.trace table { width:100%; border-collapse:collapse; }
.trace th { text-align:left; color:var(--muted); font-weight:500; font-size:12px; padding:8px; border-bottom:1px solid var(--line); position:sticky; top:0; background:var(--panel); }
.trace td { padding:7px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
.trace tbody tr:not(.detail) { cursor:pointer; }
.trace tbody tr:not(.detail):hover { background:var(--hover); }
.trace td:first-child { white-space:nowrap; color:var(--muted); font-variant-numeric:tabular-nums; }
.trace td:nth-child(2) { white-space:nowrap; }
.trace td.sum { word-break:break-word; }
.trace .caret { color:var(--muted); margin-right:6px; }
.trace tr.bad td.sum { color:var(--bad); }
.trace tr.detail pre { margin:0; font-family:var(--mono); font-size:12px; background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:10px 12px; overflow-x:auto; max-height:360px; }
.trace .add { color:var(--good); } .trace .del { color:var(--bad); }
.trace .perf { margin-bottom:16px; }
.trace .perf-sub { margin:-4px 0 12px; }
.trace .perf h3 { font-size:12px; font-weight:600; margin:4px 0 8px; color:var(--ink-soft); }
.trace .perf-tiles { display:grid; grid-template-columns:repeat(2, minmax(0,1fr)) auto; gap:12px; align-items:center; margin-bottom:16px; }
.trace .perf-tile { padding-bottom:12px; }
.trace .perf-nums { display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:8px; margin-top:6px; }
.trace .perf-nums span { display:block; color:var(--muted); font-size:11px; }
.trace .delta { font-weight:600; font-variant-numeric:tabular-nums; padding:6px 12px; border-radius:999px; white-space:nowrap;
  background:color-mix(in srgb, var(--c) 14%, transparent); color:var(--c); --c:var(--good); }
.trace .delta.up { --c:#e5a50a; }
.trace .recharts-legend-item-text { color:var(--ink-soft) !important; }
@media (max-width: 1000px) { .trace .perf-tiles { grid-template-columns:minmax(0,1fr); justify-items:start; } .trace .kpis { grid-template-columns:repeat(2, minmax(0,1fr)); } .trace .grid { grid-template-columns:minmax(0,1fr); } }
@media (max-width: 600px) {
  .trace .top, .trace main { padding-left:16px; padding-right:16px; } .trace .tabs { padding:0 16px; }
  .trace .kpi b { font-size:18px; }
  .trace td:nth-child(2), .trace th:nth-child(2) { display:none; }
  .trace .list li { grid-template-columns:1fr auto; } .trace .list em { grid-column:1 / -1; white-space:normal; }
}
`;
