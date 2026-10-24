"use client";

import { Fragment, useEffect, useMemo, useState } from "react";

type Ev = { seq: number; ts: number; kind: string; user: string; [k: string]: unknown };
type Flow = string[]; // one path through the columns, e.g. [user, route, hook, outcome]

const CAP = 5000;
const WINDOWS: Record<string, number> = { "5 min": 300, "1 h": 3600, all: Infinity };

const s = (v: unknown) => (v == null ? "" : String(v));
const route = (e: Ev) => `${s(e.method)} ${s(e.path).split("?")[0].replace(/\/(\d+|[0-9a-f-]{8,})(?=\/|$)/gi, "/{id}")}`;
const failed = (e: Ev) => e.kind.endsWith(".failed") || e.kind === "hook.rejected" || Number(e.status) >= 500;

function summary(e: Ev): string {
  switch (e.kind) {
    case "request.proxied":
      return `${route(e)} → ${s(e.status)} in ${s(e.ms)} ms${(e.hooks as string[])?.length ? ` · ${(e.hooks as string[]).join(", ")}` : ""}`;
    case "hook.applied":
      return `${s(e.hook)} on ${s(e.route)} (${s(e.ms)} ms)`;
    case "hook.failed":
      return `${s(e.hook)} on ${s(e.route)}: ${s(e.error)}`;
    case "hook.rejected":
      return `${s(e.hook)}: ${s(e.reason)}`;
    case "agent.message":
    case "agent.answer":
      return s(e.text).slice(0, 160);
    case "agent.tool":
      return `${s(e.tool)} ${s(typeof e.input === "string" ? e.input : JSON.stringify(e.input ?? "")).slice(0, 120)}`;
    default:
      return s(e.hook ?? e.feature_id ?? e.route);
  }
}

function requestFlows(events: Ev[]): Flow[] {
  return events
    .filter((e) => e.kind === "request.proxied")
    .flatMap((e) => {
      const hooks = (e.hooks as string[] | undefined)?.length ? (e.hooks as string[]) : ["no hook"];
      const st = Number(e.status);
      const outcome = st >= 500 ? "5xx" : st >= 400 ? "4xx" : st >= 300 ? "3xx" : "2xx";
      return hooks.map((h) => [e.user, route(e), h, outcome]);
    })
    .concat(events.filter((e) => e.kind === "hook.failed").map((e) => [e.user, s(e.route), s(e.hook), "failed"]));
}

function agentFlows(events: Ev[]): Flow[] {
  return events
    .filter((e) => e.kind === "agent.tool" || e.kind === "hook.saved" || e.kind === "hook.rejected")
    .map((e) => (e.kind === "agent.tool" ? [e.user, s(e.tool), "called"] : [e.user, "write_hook", e.kind === "hook.saved" ? "saved" : "rejected"]));
}

const COLORS: Record<string, string> = { "2xx": "#4cc47e", "3xx": "#38bdf8", "4xx": "#f5b544", "5xx": "#ff6e5f", failed: "#ff6e5f", rejected: "#ff6e5f", saved: "#4cc47e" };

// ponytail: hand-written column Sankey (no d3-sankey); nodes are not reordered to minimise crossings.
function Sankey({ flows, columns }: { flows: Flow[]; columns: string[] }) {
  const [hover, setHover] = useState<string | null>(null);
  const W = 900, H = 360, NW = 12, GAP = 8;
  const layout = useMemo(() => {
    const links = new Map<string, number>();
    const nodes = columns.map(() => new Map<string, { in: number; out: number }>());
    for (const f of flows)
      f.forEach((name, c) => {
        const n = nodes[c].get(name) ?? { in: 0, out: 0 };
        if (c > 0) n.in++;
        if (c < f.length - 1) {
          n.out++;
          const k = `${c}\u0000${name}\u0000${f[c + 1]}`;
          links.set(k, (links.get(k) ?? 0) + 1);
        }
        nodes[c].set(name, n);
      });
    const total = Math.max(1, ...nodes.map((col) => [...col.values()].reduce((a, n) => a + Math.max(n.in, n.out), 0)));
    const maxCount = Math.max(1, ...nodes.map((c) => c.size));
    const k = (H - GAP * (maxCount - 1)) / total;
    const pos = nodes.map((col, c) => {
      let y = 0;
      const x = (c * (W - NW)) / Math.max(1, columns.length - 1);
      return new Map(
        [...col.entries()].sort((a, b) => Math.max(b[1].in, b[1].out) - Math.max(a[1].in, a[1].out)).map(([name, n]) => {
          const h = Math.max(2, Math.max(n.in, n.out) * k);
          const p = { x, y, h, v: Math.max(n.in, n.out), outY: y, inY: y };
          y += h + GAP;
          return [name, p];
        }),
      );
    });
    const paths = [...links.entries()].sort((a, b) => b[1] - a[1]).map(([key, v]) => {
      const [c, from, to] = key.split("\u0000");
      const a = pos[+c].get(from)!, b = pos[+c + 1].get(to)!;
      const w = v * k, y0 = a.outY + w / 2, y1 = b.inY + w / 2;
      a.outY += w;
      b.inY += w;
      const x0 = a.x + NW, x1 = b.x, mx = (x0 + x1) / 2;
      return { key, from, to, v, w, d: `M${x0},${y0}C${mx},${y0} ${mx},${y1} ${x1},${y1}`, color: COLORS[to] ?? "var(--brand)" };
    });
    return { pos, paths };
  }, [flows, columns]);

  if (!flows.length) return <p className="t-empty">No traffic in this window yet.</p>;
  return (
    <div className="t-sankey">
      <div className="t-cols">{columns.map((c) => <span key={c}>{c}</span>)}</div>
      <svg viewBox={`-4 -4 ${W + 8} ${H + 8}`} role="img" aria-label="Flow diagram">
        {layout.paths.map((p) => (
          <path key={p.key} d={p.d} stroke={p.color} strokeWidth={Math.max(1, p.w)} fill="none"
            opacity={hover && hover !== p.from && hover !== p.to ? 0.08 : 0.35}>
            <title>{`${p.from} → ${p.to}: ${p.v}`}</title>
          </path>
        ))}
        {layout.pos.map((col, c) =>
          [...col.entries()].map(([name, n]) => (
            <g key={`${c}${name}`} onMouseEnter={() => setHover(name)} onMouseLeave={() => setHover(null)}>
              <rect x={n.x} y={n.y} width={NW} height={n.h} rx={3} fill={COLORS[name] ?? "var(--brand)"}>
                <title>{`${name}: ${n.v}`}</title>
              </rect>
              <text x={c === layout.pos.length - 1 ? n.x - 6 : n.x + NW + 6} y={n.y + n.h / 2} dy="0.35em"
                textAnchor={c === layout.pos.length - 1 ? "end" : "start"}>
                {name.length > 34 ? `${name.slice(0, 33)}…` : name} · {n.v}
              </text>
            </g>
          )),
        )}
      </svg>
    </div>
  );
}

export default function Traceability() {
  const [events, setEvents] = useState<Ev[]>([]);
  const [admin, setAdmin] = useState(false);
  const [signedOut, setSignedOut] = useState(false);
  const [user, setUser] = useState("");
  const [win, setWin] = useState("1 h");
  const [kind, setKind] = useState("");
  const [view, setView] = useState<"requests" | "agent">("requests");
  const [open, setOpen] = useState<number | null>(null);

  useEffect(() => {
    let after = 0, stop = false, timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const r = await fetch(`/fitit/trace/events?after=${after}&limit=1000`, { credentials: "same-origin" });
        if (r.status === 401) return setSignedOut(true);
        if (r.ok) {
          const body: { events: Ev[]; next: number; admin: boolean } = await r.json();
          after = body.next ?? after;
          setAdmin(body.admin);
          setSignedOut(false);
          if (body.events.length) setEvents((prev) => prev.concat(body.events).slice(-CAP));
        }
      } catch {
        // network blip: retry on the next tick
      }
      if (!stop) timer = setTimeout(poll, 3000);
    };
    poll();
    return () => { stop = true; clearTimeout(timer); };
  }, []);

  const users = useMemo(() => [...new Set(events.map((e) => e.user))].sort(), [events]);
  const kinds = useMemo(() => [...new Set(events.map((e) => e.kind))].sort(), [events]);
  const shown = useMemo(() => {
    const since = Date.now() / 1000 - WINDOWS[win];
    return events.filter((e) => e.ts >= since && (!user || e.user === user) && (!kind || e.kind === kind));
  }, [events, win, user, kind]);

  const count = (k: string) => shown.filter((e) => e.kind === k).length;
  const tokens = (f: string) => shown.reduce((a, e) => a + (e.kind === "agent.answer" ? Number(e[f]) || 0 : 0), 0);
  const stats: [string, number | string][] = [
    ["Requests", count("request.proxied")],
    ["Hooks applied", count("hook.applied")],
    ["Failures", shown.filter(failed).length],
    ["Agent messages", count("agent.message")],
    ["Tokens in / out", `${tokens("input_tokens")} / ${tokens("output_tokens")}`],
    ...(admin ? [["Users", new Set(shown.map((e) => e.user)).size] as [string, number]] : []),
  ];

  return (
    <main className="trace">
      <style>{CSS}</style>
      <header className="t-head">
        <h1>Traceability</h1>
        <span className="t-live">live</span>
      </header>
      {signedOut ? (
        <p className="t-empty">Log in to the app to see traceability.</p>
      ) : (
        <>
          <section className="t-stats">
            {stats.map(([label, v]) => (
              <div key={label} className={label === "Failures" && Number(v) > 0 ? "bad" : ""}>
                <b>{v}</b>
                <span>{label}</span>
              </div>
            ))}
          </section>

          <section className="t-filters">
            {admin && (
              <select value={user} onChange={(e) => setUser(e.target.value)} aria-label="User">
                <option value="">All users</option>
                {users.map((u) => <option key={u}>{u}</option>)}
              </select>
            )}
            <select value={win} onChange={(e) => setWin(e.target.value)} aria-label="Time window">
              {Object.keys(WINDOWS).map((w) => <option key={w} value={w}>{w === "all" ? "All time" : `Last ${w}`}</option>)}
            </select>
            <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind">
              <option value="">All kinds</option>
              {kinds.map((k) => <option key={k}>{k}</option>)}
            </select>
            <div className="t-toggle">
              <button aria-pressed={view === "requests"} onClick={() => setView("requests")}>Requests</button>
              <button aria-pressed={view === "agent"} onClick={() => setView("agent")}>Agent</button>
            </div>
          </section>

          <section className="t-card">
            {view === "requests" ? (
              <Sankey flows={requestFlows(shown)} columns={["User", "Route", "Hook / feature", "Outcome"]} />
            ) : (
              <Sankey flows={agentFlows(shown)} columns={["User", "Tool", "Outcome"]} />
            )}
          </section>

          <section className="t-card">
            <table className="t-table">
              <thead>
                <tr><th>Time</th><th>User</th><th>Kind</th><th>Summary</th></tr>
              </thead>
              <tbody>
                {shown.slice(-200).reverse().map((e) => (
                  <Fragment key={e.seq}>
                    <tr className={failed(e) ? "bad" : ""} onClick={() => e.kind === "hook.saved" && setOpen(open === e.seq ? null : e.seq)}
                      style={e.kind === "hook.saved" ? { cursor: "pointer" } : undefined}>
                      <td>{new Date(e.ts * 1000).toLocaleTimeString()}</td>
                      <td>{e.user}</td>
                      <td><code className={s(e.hook).startsWith("@") || e.kind.startsWith("feature.") ? "feat" : ""}>{e.kind}</code></td>
                      <td>{e.kind === "hook.saved" ? `${open === e.seq ? "▾" : "▸"} ` : ""}{summary(e)}</td>
                    </tr>
                    {open === e.seq && (
                      <tr className="t-diff">
                        <td colSpan={4}>
                          <pre>{s(e.diff).split("\n").map((l, i) => (
                            <div key={i} className={l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : ""}>{l}</div>
                          ))}</pre>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
            {!shown.length && <p className="t-empty">No events match these filters.</p>}
          </section>
        </>
      )}
    </main>
  );
}

const CSS = `
html, body { background: #f6f5f2; height: auto; }
.trace { --ink:#1c1b19; --ink-soft:#57534e; --line:#e2dfd9; --surface:#ffffff; --sunk:#efede9; --brand:#0284c7; --bad:#dc2626;
  color: var(--ink); max-width: 1200px; margin: 0 auto; padding: 24px 16px 48px; font-family: var(--sans); }
@media (prefers-color-scheme: dark) {
  html, body { background: #151412; }
  .trace { --ink:#edebe7; --ink-soft:#b5b1aa; --line:#2e2d2a; --surface:#1c1b19; --sunk:#262522; --brand:#38bdf8; --bad:#ff9a8f; color-scheme: dark; }
}
.t-head { display:flex; align-items:center; gap:12px; margin-bottom:16px; }
.t-head h1 { font-size:22px; margin:0; letter-spacing:-0.01em; }
.t-live { font-size:12px; color:#4cc47e; display:flex; align-items:center; gap:6px; }
.t-live::before { content:""; width:8px; height:8px; border-radius:50%; background:#4cc47e; animation: t-pulse 2s infinite; }
@keyframes t-pulse { 50% { opacity:.3; } }
.t-stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; margin-bottom:16px; }
.t-stats div { background:var(--surface); border:1px solid var(--line); border-radius:var(--radius); padding:12px 16px; display:flex; flex-direction:column; }
.t-stats b { font-size:22px; font-variant-numeric:tabular-nums; }
.t-stats span { color:var(--ink-soft); font-size:12px; }
.t-stats .bad b { color:var(--bad); }
.t-filters { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:16px; }
.t-filters select, .t-toggle button { background:var(--surface); border:1px solid var(--line); border-radius:999px; padding:6px 12px; color:var(--ink); }
.t-toggle { display:flex; gap:4px; margin-left:auto; }
.t-toggle button[aria-pressed="true"] { background:var(--brand); border-color:var(--brand); color:#fff; }
.t-card { background:var(--surface); border:1px solid var(--line); border-radius:var(--radius); padding:16px; margin-bottom:16px; overflow-x:auto; }
.t-sankey svg { width:100%; min-width:640px; height:auto; display:block; }
.t-sankey text { font-size:12px; fill:var(--ink); pointer-events:none; }
.t-sankey path { transition: opacity .15s; mix-blend-mode: normal; }
.t-cols { display:flex; justify-content:space-between; color:var(--ink-soft); font-size:11px; text-transform:uppercase; letter-spacing:.06em; margin-bottom:8px; min-width:640px; }
.t-table { width:100%; border-collapse:collapse; font-size:13px; }
.t-table th { text-align:left; color:var(--ink-soft); font-weight:500; font-size:12px; padding:6px 8px; border-bottom:1px solid var(--line); }
.t-table td { padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
.t-table td:first-child { white-space:nowrap; font-variant-numeric:tabular-nums; color:var(--ink-soft); }
.t-table td:last-child { word-break:break-word; }
.t-table tr.bad td { color:var(--bad); }
.t-table code { font-family:var(--mono); font-size:12px; background:var(--sunk); padding:1px 6px; border-radius:6px; white-space:nowrap; }
.t-table code.feat { color:#a855f7; }
.t-diff pre { margin:0; font-family:var(--mono); font-size:12px; background:var(--sunk); border-radius:10px; padding:10px; overflow-x:auto; }
.t-diff .add { color:#16a34a; } .t-diff .del { color:var(--bad); }
.t-empty { color:var(--ink-soft); text-align:center; padding:24px; }
`;
