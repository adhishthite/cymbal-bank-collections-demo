"use client";

import {
  type DecisionFeed,
  EMPTY_DECISIONS,
  type Timing,
  type TurnRow,
  applyDecisionEvent,
  borrowerFlagLabel,
  flagLabel,
  gateLabel,
  median,
  seedDecisions,
  staleParts,
} from "@/lib/decision";
import { backoffMs, formatInr, humanize, takeFragmentParam } from "@/lib/format";
import { type LadderRow, type Rung, ladderRows } from "@/lib/ladder";
import { readSse } from "@/lib/sse";
import { isRecord, num, parseMessage, readStore, str, writeStore } from "@/lib/storage";
import { useEffect, useReducer, useRef, useState } from "react";

const TOKEN_KEY = "cymbal.view";

const PREVIEW_RUNGS: Rung[] = [
  { months: 5, amountInr: 8185 },
  { months: 3, amountInr: 4911 },
  { months: 2, amountInr: 3274 },
];

type ActiveCall = {
  callId: string;
  loanId: string;
  customerName: string;
  language: string;
  callAttempt: number | null;
  status: string;
  rungs: Rung[];
};
// "notice" is a line that the page adds itself, for example after a reconnect.
type Line = { role: "customer" | "bot" | "notice"; text: string };
type ToolEvent = {
  name: string;
  args: Record<string, unknown>;
  ok: boolean;
  error: string;
};

type Feed = {
  active: ActiveCall | null;
  liveModel: string;
  locations: unknown;
  // The call that the transcript and the tool list belong to. It stays after the call ends.
  callId: string | null;
  lines: Line[];
  // The text of a turn in progress, for each speaker. The final transcript line replaces it.
  live: { customer: string; bot: string };
  // The Ask ladder of the call. It stays after the call ends, as the transcript does.
  rungs: Rung[];
  // The last Ask that the bot said. The decision turns get it only after the borrower replies.
  askInr: number | null;
  tools: ToolEvent[];
  decisions: DecisionFeed;
  ended: { reason: string; status: string } | null;
};

const EMPTY: Feed = {
  active: null,
  liveModel: "",
  locations: null,
  callId: null,
  lines: [],
  live: { customer: "", bot: "" },
  rungs: [],
  askInr: null,
  tools: [],
  decisions: EMPTY_DECISIONS,
  ended: null,
};

// A new call_id clears the transcript, the tool list and the decision panel of the previous call.
function forCall(feed: Feed, callId: string): Feed {
  return feed.callId === callId
    ? feed
    : {
        ...feed,
        callId,
        lines: [],
        live: EMPTY.live,
        rungs: [],
        askInr: null,
        tools: [],
        decisions: EMPTY_DECISIONS,
        ended: null,
      };
}

function reduce(feed: Feed, msg: Record<string, unknown> & { type: string }): Feed {
  switch (msg.type) {
    case "state": {
      const a = isRecord(msg.active_call) ? msg.active_call : null;
      const active: ActiveCall | null = a && {
        callId: str(a.call_id),
        loanId: str(a.loan_id),
        customerName: str(a.customer_name),
        language: str(a.language),
        callAttempt: num(a.call_attempt),
        status: str(a.status),
        rungs: Array.isArray(a.rungs)
          ? a.rungs.filter(isRecord).map((r) => ({
              months: num(r.months) ?? 0,
              amountInr: num(r.amount_inr) ?? 0,
            }))
          : [],
      };
      let next = active ? forCall(feed, active.callId) : feed;
      // The call ended while the stream was down, so its call_ended event was lost.
      if (!active && next.callId && !next.ended)
        next = { ...next, ended: { reason: "unknown", status: "" } };
      // The state event holds the decision panel of the active call. Seed it again on each reconnect.
      if (active) next = { ...next, decisions: seedDecisions(msg.decisions), rungs: active.rungs };
      return { ...next, active, liveModel: str(msg.live_model), locations: msg.locations };
    }
    case "transcript": {
      const next = forCall(feed, str(msg.call_id));
      const role = msg.role === "customer" ? "customer" : "bot";
      return {
        ...next,
        lines: [...next.lines, { role, text: str(msg.text) }],
        live: { ...next.live, [role]: "" },
      };
    }
    case "caption": {
      const next = forCall(feed, str(msg.call_id));
      const role = msg.role === "customer" ? "customer" : "bot";
      return { ...next, live: { ...next.live, [role]: str(msg.text) } };
    }
    case "ask": {
      const next = forCall(feed, str(msg.call_id));
      return { ...next, askInr: num(msg.ask_inr) };
    }
    case "transcript_update": {
      // Late text of a bot turn that the borrower cut off. It completes the last bot line.
      const next = forCall(feed, str(msg.call_id));
      const i = next.lines.findLastIndex((l) => l.role === "bot");
      if (i < 0) return next;
      const lines = next.lines.with(i, { role: "bot", text: str(msg.text) });
      return { ...next, lines };
    }
    case "tool": {
      const next = forCall(feed, str(msg.call_id));
      const tool = {
        name: str(msg.name),
        args: isRecord(msg.args) ? msg.args : {},
        ok: msg.ok === true,
        error: str(msg.error),
      };
      return {
        ...next,
        tools: [...next.tools, tool],
        decisions: applyDecisionEvent(next.decisions, msg),
      };
    }
    case "decision":
    case "consent":
    case "gate":
    case "audit": {
      const next = forCall(feed, str(msg.call_id));
      return { ...next, decisions: applyDecisionEvent(next.decisions, msg) };
    }
    case "call_ended": {
      const next = forCall(feed, str(msg.call_id));
      return {
        ...next,
        live: EMPTY.live,
        ended: { reason: str(msg.reason), status: str(msg.status) },
      };
    }
    case "_reconnected":
      if (!feed.callId) return feed;
      return {
        ...feed,
        lines: [
          ...feed.lines,
          { role: "notice", text: "Stream reconnected. Some lines may be missing." },
        ],
      };
    default:
      return feed;
  }
}

type Conn = "connecting" | "live" | "reconnecting" | "no-link" | "denied";

export default function MonitorPage() {
  const [feed, dispatch] = useReducer(reduce, EMPTY);
  const [conn, setConn] = useState<Conn>("connecting");
  const transcriptEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const abort = new AbortController();
    (async () => {
      // Keep the view token for reloads in this tab. Auto-mint via demo endpoint if absent.
      const fromLink = takeFragmentParam("t");
      if (fromLink) writeStore("session", TOKEN_KEY, fromLink);
      let token = fromLink ?? readStore("session", TOKEN_KEY);

      async function fetchDemoToken(): Promise<string | null> {
        try {
          const res = await fetch("/api/demo/monitor-link", {
            method: "POST",
            signal: abort.signal,
          });
          if (!res.ok) return null;
          const data: unknown = await res.json();
          if (isRecord(data) && typeof data.token === "string" && data.token) {
            writeStore("session", TOKEN_KEY, data.token);
            return data.token;
          }
        } catch {
          // Ignore network error during bootstrap.
        }
        return null;
      }

      if (!token) {
        token = await fetchDemoToken();
      }
      if (!token) {
        setConn("no-link");
        return;
      }

      let attempt = 0;
      let connectedBefore = false;
      while (!abort.signal.aborted) {
        try {
          const res = await fetch("/api/monitor/stream", {
            headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
            cache: "no-store",
            signal: abort.signal,
          });
          if (res.status === 401 || res.status === 403) {
            const refreshed = await fetchDemoToken();
            if (refreshed) {
              token = refreshed;
              continue;
            }
            setConn("denied");
            return;
          }
          if (res.ok && res.body) {
            setConn("live");
            attempt = 0;
            if (connectedBefore) dispatch({ type: "_reconnected" });
            connectedBefore = true;
            await readSse(res.body, (data) => {
              const msg = parseMessage(data);
              if (msg) dispatch(msg);
            });
          }
        } catch {
          if (abort.signal.aborted) return;
        }
        setConn("reconnecting");
        await new Promise((r) => setTimeout(r, backoffMs(attempt++)));
      }
    })();
    return () => abort.abort();
  }, []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: scroll when a transcript line arrives.
  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ block: "end" });
  }, [feed.lines.length, feed.live.customer, feed.live.bot]);

  if (conn === "no-link" || conn === "denied") {
    return (
      <main className="monitor">
        <section className="screen center">
          <h1>Monitor link needed</h1>
          <p className="muted lead">
            {conn === "denied"
              ? "This monitor link is not valid or it expired. Make a new monitor link."
              : "Open this page with a monitor link."}
          </p>
        </section>
      </main>
    );
  }

  const call = feed.active;
  const turns = feed.decisions.turns;
  const link = [...feed.tools]
    .reverse()
    .find((t) => t.ok && t.name === "generate_upi_payment_link");
  const activeRungs = feed.rungs.length > 0 ? feed.rungs : PREVIEW_RUNGS;
  const ladder = ladderRows(
    activeRungs,
    [...turns.map((t) => t.askInr), feed.askInr],
    turns.at(-1)?.offerInr ?? null,
    link ? num(link.args.amount_inr) : null,
  );
  return (
    <main className="monitor">
      <header className="monitor-bar">
        <span className="brand">
          <span className="brand-name">Cymbal Bank</span>
          <span className="brand-sub">Live call monitor &amp; negotiation ladder</span>
        </span>
        <div className="monitor-bar-right">
          <a href="/" className="monitor-nav-link">
            ← Operator Cockpit
          </a>
          <a href="/mobile/" target="_blank" rel="noreferrer" className="monitor-nav-link">
            Web Phone (/mobile) ↗
          </a>
          <span className={conn === "live" ? "pill pill-ok" : "pill pill-warn"}>
            <span className="pill-dot" aria-hidden />
            {conn === "live" ? "Live" : conn === "connecting" ? "Connecting…" : "Reconnecting…"}
          </span>
        </div>
      </header>

      <section className="call-head">
        {call ? (
          <>
            <h1 className="call-head-name">{call.customerName}</h1>
            <dl className="call-facts">
              <div>
                <dt>Loan</dt>
                <dd className="num">{call.loanId}</dd>
              </div>
              <div>
                <dt>Language</dt>
                <dd>{call.language}</dd>
              </div>
              <div>
                <dt>Call</dt>
                <dd>{attemptLabel(call.callAttempt)}</dd>
              </div>
            </dl>
            <span className={`chip chip-${statusTone(call.status)}`}>
              <span className="chip-dot" aria-hidden />
              {humanize(call.status)}
            </span>
          </>
        ) : feed.ended ? (
          <>
            <h1 className="call-head-name call-head-idle">
              Call ended: {humanize(feed.ended.reason).toLowerCase()}
            </h1>
            {feed.ended.status && (
              <span className={`chip chip-${statusTone(feed.ended.status)}`}>
                <span className="chip-dot" aria-hidden />
                {humanize(feed.ended.status)}
              </span>
            )}
          </>
        ) : (
          <>
            <h1 className="call-head-name call-head-idle">Waiting for a call</h1>
            <span className="chip chip-neutral">
              Dial any account from the Operator Cockpit (/) to stream live
            </span>
          </>
        )}
      </section>

      <div className="monitor-grid">
        <section className="stage" aria-label="Conversation">
          {feed.lines.length === 0 && !feed.live.bot && !feed.live.customer && (
            <p className="stage-empty">
              The conversation appears here, word by word, as the call runs.
            </p>
          )}
          <div className="script">
            {feed.lines.map((line, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: append-only list.
              <Said key={i} role={line.role} text={line.text} />
            ))}
            {(["bot", "customer"] as const).map(
              (role) =>
                feed.live[role] && <Said key={role} role={role} text={feed.live[role]} live />,
            )}
          </div>
          <div ref={transcriptEnd} />
        </section>

        <aside className="rail">
          <Ladder rows={ladder} isPreview={feed.rungs.length === 0} />
          <section className="rail-block">
            <h2>Actions</h2>
            {feed.tools.length === 0 && <p className="rail-empty">No action yet.</p>}
            <ol className="tools">
              {feed.tools.map((t, i) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: append-only list.
                <li key={i} className={t.ok ? "tool tool-ok" : "tool tool-err"}>
                  <span className="tool-name">{toolLabel(t)}</span>
                  <span className="tool-result">{t.ok ? "Done" : "Rejected"}</span>
                  {!t.ok && t.error && <span className="tool-error">{t.error}</span>}
                </li>
              ))}
            </ol>
          </section>
          <DecisionPanel d={feed.decisions} />
        </aside>
      </div>

      <footer className="monitor-foot">
        <div className="foot-item">
          <span className="foot-key">Voice model</span>
          <span className="foot-val">{feed.liveModel || "gemini-3.1-flash-live-preview"}</span>
        </div>
        <Locations value={feed.locations} />
      </footer>
    </main>
  );
}

// One line of the conversation. A live line is a turn in progress and grows word by word.
function Said({ role, text, live }: { role: Line["role"]; text: string; live?: boolean }) {
  if (role === "notice") return <p className="said said-notice">{text}</p>;
  return (
    <div className={`said said-${role}${live ? " said-live" : ""}`}>
      <span className="said-who">{role === "customer" ? "Customer" : "AI agent"}</span>
      <p className="said-text">
        {text}
        {live && <span className="caret" aria-hidden />}
      </p>
    </div>
  );
}

const RUNG_STATE: Record<LadderRow["state"], string> = {
  passed: "Refused",
  current: "Asking now",
  ahead: "",
  agreed: "Link sent",
  offer: "Customer offer",
};

function Ladder({ rows, isPreview }: { rows: LadderRow[]; isPreview?: boolean }) {
  if (rows.length === 0) return null;
  const floor = Math.min(...rows.filter((r) => r.months !== null).map((r) => r.amountInr));
  return (
    <section className="rail-block ladder">
      <h2>{isPreview ? "Ask ladder (Scenario B preview)" : "Ask ladder"}</h2>
      <ol className="rungs">
        {rows.map((r) => (
          <li key={`${r.amountInr}-${r.state}`} className={`rung rung-${r.state}`}>
            <span className="rung-amount num">{formatInr(r.amountInr)}</span>
            <span className="rung-what">
              {r.months === null
                ? "Own amount"
                : r.amountInr === floor && rows.length > 1
                  ? `${r.months} months, the floor`
                  : `${r.months} months`}
            </span>
            {RUNG_STATE[r.state] && <span className="rung-state">{RUNG_STATE[r.state]}</span>}
          </li>
        ))}
      </ol>
    </section>
  );
}

function attemptLabel(n: number | null): string {
  if (n === 1) return "First reminder";
  if (n === 2) return "Second call";
  if (n === 3) return "Final call";
  return "—";
}

// Display only: a plain label for a Live tool call. An unknown tool shows its raw name.
function toolLabel(t: ToolEvent): string {
  const amount = num(t.args.amount_inr);
  const days = num(t.args.days_from_today);
  const settlement = str(t.args.settlement_type);
  switch (t.name) {
    case "generate_upi_payment_link": {
      const parts = ["Payment link"];
      if (amount !== null) parts.push(formatInr(amount));
      if (settlement === "FULL_DUE") parts.push("Full due");
      else if (settlement === "PARTIAL_TWO_MONTH") parts.push("2-month partial");
      return parts.join(", ");
    }
    case "schedule_callback_reminder":
      return days === null ? "Callback" : `Callback in ${days} ${days === 1 ? "day" : "days"}`;
    case "escalate_to_branch_manager":
      return "Escalated to branch";
    default:
      return t.name;
  }
}

// Display only: the chip colour for an account status.
function statusTone(status: string): "live" | "good" | "attention" | "neutral" {
  if (status === "CALL_IN_PROGRESS") return "live";
  if (
    status === "PAYMENT_LINK_SENT_TODAY" ||
    status === "PARTIAL_SETTLEMENT_AGREED" ||
    status === "CALLBACK_SCHEDULED"
  )
    return "good";
  if (status === "DISPUTE_LOGGED" || status === "ESCALATED_BRANCH_HARDSHIP") return "attention";
  return "neutral";
}

// The spec does not fix the shape of "locations". Show each map key as one item, and anything else as text.
function Locations({ value }: { value: unknown }) {
  if (value == null) return null;
  if (!isRecord(value))
    return (
      <div className="foot-item">
        <span className="foot-val">{str(value)}</span>
      </div>
    );
  return (
    <>
      {Object.entries(value).map(([k, v]) => (
        <div key={k} className="foot-item">
          <span className="foot-key">{humanize(k.replace(/^GENAI_LOCATION_/, ""))}</span>
          <span className="foot-val">{str(v)}</span>
        </div>
      ))}
    </>
  );
}

const ms = (v: number | null): string => (v === null ? "—" : `${Math.round(v)} ms`);

function Strip({ label, t }: { label: string; t: Timing }) {
  return (
    <div className="strip-item">
      <dt>{label}</dt>
      <dd className="num">
        {t.n === 0 ? "—" : `${ms(median(t.samples))} median, ${ms(t.max)} max`}
      </dd>
    </div>
  );
}

// A meter for a 0 to 3 score. An unknown score shows "—" and an empty bar.
function Meter({ label, value }: { label: string; value: number | null }) {
  const v = value === null ? 0 : Math.min(3, Math.max(0, value));
  return (
    <div className="meter">
      <span className="meter-label">{label}</span>
      <span className="meter-track" aria-hidden>
        <span className="meter-fill" style={{ width: `${(v / 3) * 100}%` }} />
      </span>
      <span className="meter-val num">{value === null ? "—" : `${v.toFixed(1)} / 3`}</span>
    </div>
  );
}

function consentTone(c: TurnRow["consent"]): "good" | "attention" | "neutral" {
  if (c?.status !== "ready") return "neutral";
  return c.decision === "AGREE_TODAY" ? "good" : "attention";
}

function DecisionPanel({ d }: { d: DecisionFeed }) {
  const rows = [...d.turns].reverse().slice(0, 6);
  const latest = rows[0];
  const latestStale = latest ? staleParts(latest).decision : false;
  // Audit flags are about the bot. The newest five show with the flags of the latest borrower turn.
  const flags = [
    ...(latest && !latestStale ? latest.flags.map(borrowerFlagLabel) : []),
    ...d.audits
      .slice(-5)
      .flatMap((a) => a.flags)
      .map(flagLabel),
  ];
  return (
    <section className="rail-block decision">
      <h2>Decision model</h2>
      {rows.length === 0 && <p className="rail-empty">Reads each customer reply.</p>}
      <ol className="turns">
        {rows.map((t) => {
          const stale = staleParts(t);
          return (
            <li key={t.turnId} className="turn">
              <span className="turn-no num">Turn {t.turnId}</span>
              {stale.decision ? (
                <span className="chip chip-neutral">Pending</span>
              ) : t.status === "ready" ? (
                <span className="chip chip-neutral">
                  {humanize(t.act) || "—"}
                  {t.actConfidence !== null && ` ${Math.round(t.actConfidence * 100)}%`}
                </span>
              ) : (
                <span className="chip chip-neutral">Unavailable</span>
              )}
              {stale.decision ? (
                <span className="turn-facts">Waiting for the decision model</span>
              ) : (
                <dl className="turn-facts num">
                  <div>
                    <dt>Offer</dt>
                    <dd>{t.offerInr === null ? "—" : formatInr(t.offerInr)}</dd>
                  </div>
                  <div>
                    <dt>Pays</dt>
                    <dd>{humanize(t.paymentTime) || "—"}</dd>
                  </div>
                  <div>
                    <dt>Refusals</dt>
                    <dd>{t.refusals ?? "—"} of 2</dd>
                  </div>
                </dl>
              )}
              <span className={`chip chip-${stale.consent ? "neutral" : consentTone(t.consent)}`}>
                Consent:{" "}
                {stale.consent
                  ? "pending"
                  : t.consent?.status === "ready"
                    ? `${humanize(t.consent.decision)}${t.consent.amountInr === null ? "" : ` ${formatInr(t.consent.amountInr)}`}`
                    : t.consent
                      ? "unavailable"
                      : "—"}
              </span>
            </li>
          );
        })}
      </ol>
      {flags.length > 0 && (
        <ul className="flags" aria-label="Flags">
          {flags.map((f, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: the same flag can show twice.
            <li key={i} className="chip chip-attention">
              {f}
            </li>
          ))}
        </ul>
      )}
      <Meter label="Frustration" value={latest?.frustration ?? null} />
      <Meter label="Willingness" value={latest?.willingness ?? null} />
      <ul className="gates">
        {[...d.gates]
          .reverse()
          .slice(0, 6)
          .map((g, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: log lines have no id.
            <li key={i} className={g.allowed ? "gate gate-ok" : "gate gate-block"}>
              {gateLabel(g)}
            </li>
          ))}
      </ul>
      <dl className="strip">
        <Strip label="Decision model" t={d.decisionMs} />
        <Strip label="Consent check" t={d.consentMs} />
        <div className="strip-item">
          <dt>Wait at tool time</dt>
          <dd className="num">
            {d.wait.n === 0 ? "—" : `${ms(d.wait.total)} total, ${ms(d.wait.max)} max`}
          </dd>
        </div>
      </dl>
    </section>
  );
}
