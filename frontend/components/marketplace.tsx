"use client";

import { useEffect, useRef, useState } from "react";
import {
  errorText,
  forkFeature,
  getMarketplaceFeature,
  listMarketplace,
  reloadHostPage,
  reportFeature,
  SignedOut,
  subscribeFeature,
  type MarketplaceCard,
  type MarketplaceSort,
} from "../lib/core";

type MarketplaceProps = {
  onSignedOut: () => void;
};

function aborted(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function subscriberLabel(count: number): string {
  return count === 1 ? "1 subscriber" : `${count} subscribers`;
}

export function Marketplace({ onSignedOut }: MarketplaceProps) {
  const onSignedOutRef = useRef(onSignedOut);
  onSignedOutRef.current = onSignedOut;
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<MarketplaceSort>("subscribers");
  const [cards, setCards] = useState<MarketplaceCard[]>([]);
  const [phase, setPhase] = useState<"idle" | "ready" | "unavailable">("idle");
  const [loading, setLoading] = useState(false);
  const [pendingId, setPendingId] = useState("");
  const [actionError, setActionError] = useState<{ id: string; message: string } | null>(null);
  const [forkId, setForkId] = useState<string | null>(null);
  const [forkName, setForkName] = useState("");
  const [reportId, setReportId] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [reportedId, setReportedId] = useState<string | null>(null);
  const [codeId, setCodeId] = useState<string | null>(null);
  const [codeText, setCodeText] = useState("");
  const [codePublished, setCodePublished] = useState(true);
  const [codeState, setCodeState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [codeNote, setCodeNote] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    const timer = window.setTimeout(() => {
      listMarketplace(query.trim(), sort, controller.signal)
        .then((next) => {
          if (controller.signal.aborted) return;
          setCards(next);
          setPhase("ready");
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted || aborted(error)) return;
          if (error instanceof SignedOut) {
            onSignedOutRef.current();
            return;
          }
          setCards([]);
          setPhase("unavailable");
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 250);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [query, sort]);

  function fail(id: string, error: unknown, fallback: string) {
    if (error instanceof SignedOut) onSignedOutRef.current();
    else setActionError({ id, message: errorText(error, fallback) });
    setPendingId("");
  }

  async function subscribe(id: string) {
    setPendingId(id);
    setActionError(null);
    try {
      await subscribeFeature(id);
    } catch (error) {
      fail(id, error, "Could not subscribe.");
      return;
    }
    reloadHostPage();
  }

  async function fork(id: string) {
    setPendingId(id);
    setActionError(null);
    try {
      await forkFeature(id, forkName);
    } catch (error) {
      fail(id, error, "Could not fork this feature.");
      return;
    }
    reloadHostPage();
  }

  async function report(id: string) {
    const nextReason = reason.trim();
    if (!nextReason) {
      setActionError({ id, message: "Add a reason." });
      return;
    }
    setPendingId(id);
    setActionError(null);
    try {
      await reportFeature(id, nextReason);
      setReportedId(id);
      setReportId(null);
      setReason("");
    } catch (error) {
      fail(id, error, "Could not report this feature.");
      return;
    }
    setPendingId("");
  }

  async function toggleCode(id: string) {
    if (codeId === id) {
      setCodeId(null);
      return;
    }
    setCodeId(id);
    setCodeState("loading");
    setCodeText("");
    setCodeNote("");
    try {
      const detail = await getMarketplaceFeature(id);
      setCodeText(detail.code);
      setCodePublished(detail.published);
      setCodeState("ready");
    } catch (error) {
      if (error instanceof SignedOut) {
        onSignedOutRef.current();
        return;
      }
      setCodeNote(errorText(error, "Could not load the code."));
      setCodeState("error");
    }
  }

  const busy = pendingId !== "";
  const trimmedQuery = query.trim();

  return (
    <div className="market">
      <form className="market-tools" role="search" onSubmit={(event) => event.preventDefault()}>
        <label className="market-search">
          Search
          <input
            value={query}
            placeholder="Title, owner, route"
            autoComplete="off"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <div className="market-filters">
          <div className="market-sort" role="group" aria-label="Sort by">
            <button
              type="button"
              aria-pressed={sort === "subscribers"}
              onClick={() => setSort("subscribers")}
            >
              Subscribers
            </button>
            <button type="button" aria-pressed={sort === "newest"} onClick={() => setSort("newest")}>
              Newest
            </button>
          </div>
          {phase === "ready" ? <span className="count">{cards.length}</span> : null}
        </div>
      </form>
      {phase === "unavailable" ? (
        <p className="market-empty" role="status">
          Marketplace is not available yet.
        </p>
      ) : null}
      {phase !== "unavailable" && loading && cards.length === 0 ? (
        <p className="market-empty">Loading the marketplace…</p>
      ) : null}
      {phase === "ready" && cards.length === 0 ? (
        <p className="market-empty">
          {trimmedQuery === "" ? "Nothing in the marketplace yet." : "No features match that search."}
        </p>
      ) : null}
      {cards.length > 0 ? (
        <ul className="market-list" aria-busy={loading}>
          {cards.map((card) => {
            const title = card.title || "Untitled";
            const showSubscribe = !card.mine && !card.subscribed;
            return (
              <li className="market-card" key={card.id}>
                <h3>{title}</h3>
                {card.description ? <p className="market-description">{card.description}</p> : null}
                <p className="market-meta">
                  {card.route ? <code title={card.route}>{card.route}</code> : null}
                  {card.owner ? <span>{card.owner}</span> : null}
                  <span>v{card.version}</span>
                  <span>{subscriberLabel(card.subscribers)}</span>
                </p>
                <div className="market-actions">
                  {card.mine ? <span className="market-state">Yours</span> : null}
                  {!card.mine && card.subscribed ? <span className="market-state">Subscribed</span> : null}
                  {showSubscribe ? (
                    <button
                      type="button"
                      className="market-subscribe"
                      aria-label={`Subscribe to ${title}`}
                      disabled={busy}
                      onClick={() => void subscribe(card.id)}
                    >
                      Subscribe
                    </button>
                  ) : null}
                  {card.mine ? null : (
                    <button
                      type="button"
                      className="ghost"
                      aria-label={`Fork ${title}`}
                      disabled={busy}
                      aria-expanded={forkId === card.id}
                      onClick={() => {
                        setForkId(forkId === card.id ? null : card.id);
                        setForkName("");
                        setReportId(null);
                        setActionError(null);
                      }}
                    >
                      Fork
                    </button>
                  )}
                  {card.mine ? null : (
                    <button
                      type="button"
                      className="ghost"
                      aria-label={`Report ${title}`}
                      disabled={busy}
                      aria-expanded={reportId === card.id}
                      onClick={() => {
                        setReportId(reportId === card.id ? null : card.id);
                        setReason("");
                        setForkId(null);
                        setActionError(null);
                      }}
                    >
                      Report
                    </button>
                  )}
                  <button
                    type="button"
                    className="ghost"
                    aria-label={codeId === card.id ? `Hide code for ${title}` : `Show code for ${title}`}
                    aria-expanded={codeId === card.id}
                    disabled={codeState === "loading" && codeId === card.id}
                    onClick={() => void toggleCode(card.id)}
                  >
                    Code
                  </button>
                </div>
                {forkId === card.id ? (
                  <form
                    className="market-form"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void fork(card.id);
                    }}
                  >
                    <label>
                      Name
                      <input
                        value={forkName}
                        placeholder="Optional"
                        autoComplete="off"
                        disabled={busy}
                        onChange={(event) => setForkName(event.target.value)}
                      />
                    </label>
                    <span className="market-actions">
                      <button type="submit" className="ghost" disabled={busy}>
                        Fork
                      </button>
                      <button type="button" className="ghost" disabled={busy} onClick={() => setForkId(null)}>
                        Cancel
                      </button>
                    </span>
                  </form>
                ) : null}
                {reportId === card.id ? (
                  <form
                    className="market-form"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void report(card.id);
                    }}
                  >
                    <label>
                      Reason
                      <textarea
                        value={reason}
                        required
                        rows={3}
                        disabled={busy}
                        onChange={(event) => setReason(event.target.value)}
                      />
                    </label>
                    <span className="market-actions">
                      <button type="submit" className="ghost" disabled={busy || reason.trim() === ""}>
                        Report
                      </button>
                      <button type="button" className="ghost" disabled={busy} onClick={() => setReportId(null)}>
                        Cancel
                      </button>
                    </span>
                  </form>
                ) : null}
                {reportedId === card.id ? <p className="market-note">Reported.</p> : null}
                {actionError?.id === card.id ? (
                  <p className="notice error" role="alert">
                    {actionError.message}
                  </p>
                ) : null}
                {codeId === card.id ? (
                  codeState === "loading" ? (
                    <p className="market-note">Loading code…</p>
                  ) : codeState === "error" ? (
                    <p className="notice error" role="alert">
                      {codeNote}
                    </p>
                  ) : (
                    <>
                      {codePublished ? null : <p className="market-note">Unpublished.</p>}
                      <pre className="market-code">
                        <code>{codeText}</code>
                      </pre>
                    </>
                  )
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
