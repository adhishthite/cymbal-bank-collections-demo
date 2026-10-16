"use client";

import { formatInr, humanize } from "@/lib/format";
import { isRecord, num, readStore, str, writeStore } from "@/lib/storage";
import { useCallback, useEffect, useState } from "react";

const THEME_KEY = "cymbal.theme";
const DEVICE_KEY = "mcd5.device";
const LANGUAGES = ["Hindi", "Malayalam", "Kannada", "Tamil", "Telugu", "English"] as const;

type Rung = { months: number; amountInr: number };

type AccountRow = {
  loanId: string;
  customerName: string;
  language: string;
  languageSource: string;
  city: string;
  branch: string;
  schemeName: string;
  dueAmountInr: number;
  botAskAmountInr: number;
  callAttempt: number;
  riskTier: string;
  status: string;
  callable: boolean;
  daysPastDue: number | null;
  monthlyInterestInr: number | null;
};

type ScenarioCard = {
  scenarioId: string;
  title: string;
  subtitle: string;
  loanId: string;
  language: string;
  highlight: string;
  sampleBorrowerUtterance: string;
  account: AccountRow;
  rungs: Rung[];
  floorInr: number;
};

type PortfolioAnalysis = {
  totalAccounts: number;
  totalDueInr: number;
  calledAccounts: number;
  outcomeRatePct: number;
  linksSentTodayInr: number;
  promisedWithin7DaysInr: number;
  totalCommittedInr: number;
  committedPct: number;
  partialAgreements: number;
  escalations: number;
};

type HandoffBrief = {
  loanId: string;
  customerName: string;
  branch: string;
  status: string;
  reasonCategory: string;
  disputedFeeInr: number;
  customerSummary: string;
  recommendedAction: string;
  branchCallbackDate: string;
};

type BootstrapState = {
  bank: string;
  telephonyMode: string;
  phoneOnline: boolean;
  phonePaired: boolean;
  liveModel: string;
  callWindowBypass: boolean;
  activeCallCustomer: string | null;
  activeCallLoanId: string | null;
  monitorToken: string;
  scenarios: ScenarioCard[];
  queueTotalAccounts: number;
  queueTotalDueInr: number;
  queueTop: AccountRow[];
  handledToday: AccountRow[];
  analysis: PortfolioAnalysis;
  handoffs: HandoffBrief[];
};

function parseAccount(raw: unknown): AccountRow | null {
  if (!isRecord(raw)) return null;
  return {
    loanId: str(raw.loan_id),
    customerName: str(raw.customer_name),
    language: str(raw.language),
    languageSource: str(raw.language_source),
    city: str(raw.city),
    branch: str(raw.branch),
    schemeName: str(raw.scheme_name),
    dueAmountInr: num(raw.due_amount_inr) ?? 0,
    botAskAmountInr: num(raw.bot_ask_amount_inr) ?? 0,
    callAttempt: num(raw.call_attempt) ?? 1,
    riskTier: str(raw.risk_tier),
    status: str(raw.status),
    callable: raw.callable === true,
    daysPastDue: num(raw.days_past_due),
    monthlyInterestInr: num(raw.monthly_interest_inr),
  };
}

function parseBootstrap(raw: unknown): BootstrapState | null {
  if (!isRecord(raw) || raw.ok !== true) return null;
  const activeCall = isRecord(raw.active_call) ? raw.active_call : null;
  const queue = isRecord(raw.queue) ? raw.queue : {};
  const analysisRaw = isRecord(raw.analysis) ? raw.analysis : {};

  const scenarios: ScenarioCard[] = Array.isArray(raw.scenarios)
    ? raw.scenarios.flatMap((item) => {
        if (!isRecord(item)) return [];
        const acc = parseAccount(item.account);
        if (!acc) return [];
        const rungs: Rung[] = Array.isArray(item.rungs)
          ? item.rungs.filter(isRecord).map((r) => ({
              months: num(r.months) ?? 0,
              amountInr: num(r.amount_inr) ?? 0,
            }))
          : [];
        return [
          {
            scenarioId: str(item.scenario_id),
            title: str(item.title),
            subtitle: str(item.subtitle),
            loanId: str(item.loan_id),
            language: str(item.language),
            highlight: str(item.highlight),
            sampleBorrowerUtterance: str(item.sample_borrower_utterance),
            account: acc,
            rungs,
            floorInr: num(item.floor_inr) ?? acc.botAskAmountInr,
          },
        ];
      })
    : [];

  const queueTop: AccountRow[] = Array.isArray(queue.top)
    ? queue.top.flatMap((r) => {
        const parsed = parseAccount(r);
        return parsed ? [parsed] : [];
      })
    : [];

  const handledToday: AccountRow[] = Array.isArray(queue.handled_today)
    ? queue.handled_today.flatMap((r) => {
        const parsed = parseAccount(r);
        return parsed ? [parsed] : [];
      })
    : [];

  const handoffs: HandoffBrief[] = Array.isArray(raw.handoffs)
    ? raw.handoffs.filter(isRecord).map((h) => ({
        loanId: str(h.loan_id),
        customerName: str(h.customer_name),
        branch: str(h.branch),
        status: str(h.status),
        reasonCategory: str(h.reason_category),
        disputedFeeInr: num(h.disputed_fee_inr) ?? 0,
        customerSummary: str(h.customer_summary),
        recommendedAction: str(h.recommended_action),
        branchCallbackDate: str(h.branch_callback_date),
      }))
    : [];

  return {
    bank: str(raw.bank) || "Cymbal Bank",
    telephonyMode: str(raw.telephony_mode) || "mobile",
    phoneOnline: raw.phone_online === true,
    phonePaired: raw.phone_paired === true,
    liveModel: str(raw.live_model) || "gemini-3.1-flash-live-preview",
    callWindowBypass: raw.call_window_bypass === true,
    activeCallCustomer: activeCall ? str(activeCall.customer_name) : null,
    activeCallLoanId: activeCall ? str(activeCall.loan_id) : null,
    monitorToken: str(raw.monitor_token),
    scenarios,
    queueTotalAccounts: num(queue.total_accounts) ?? 0,
    queueTotalDueInr: num(queue.total_due_inr) ?? 0,
    queueTop,
    handledToday,
    analysis: {
      totalAccounts: num(analysisRaw.total_accounts) ?? 0,
      totalDueInr: num(analysisRaw.total_due_inr) ?? 0,
      calledAccounts: num(analysisRaw.called_accounts) ?? 0,
      outcomeRatePct: num(analysisRaw.outcome_rate_pct) ?? 0,
      linksSentTodayInr: num(analysisRaw.links_sent_today_inr) ?? 0,
      promisedWithin7DaysInr: num(analysisRaw.promised_within_7_days_inr) ?? 0,
      totalCommittedInr: num(analysisRaw.total_committed_inr) ?? 0,
      committedPct: num(analysisRaw.committed_pct) ?? 0,
      partialAgreements: num(analysisRaw.partial_agreements) ?? 0,
      escalations: num(analysisRaw.escalations) ?? 0,
    },
    handoffs,
  };
}

export default function CockpitPage() {
  const [data, setData] = useState<BootstrapState | null>(null);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [busyLoanId, setBusyLoanId] = useState<string | null>(null);
  const [banner, setBanner] = useState<{ kind: "ok" | "warn" | "err"; text: string } | null>(null);
  const [showDock, setShowDock] = useState<boolean>(true);

  useEffect(() => {
    const saved = readStore("local", THEME_KEY);
    if (saved === "dark" || saved === "light") {
      setTheme(saved);
      document.documentElement.dataset.theme = saved;
    }
  }, []);

  const toggleTheme = () => {
    const next = theme === "light" ? "dark" : "light";
    setTheme(next);
    writeStore("local", THEME_KEY, next);
    document.documentElement.dataset.theme = next;
  };

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/demo/bootstrap", { cache: "no-store" });
      if (!res.ok) return;
      const parsed = parseBootstrap(await res.json());
      if (parsed) setData(parsed);
    } catch {
      // Keep existing state on transient error.
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 2500);
    return () => clearInterval(id);
  }, [refresh]);

  const openMobileTab = async () => {
    const existingDevice = readStore("local", DEVICE_KEY);
    if (existingDevice && data?.phoneOnline) {
      window.open("/mobile/", "_blank", "noopener,noreferrer");
      setBanner({
        kind: "ok",
        text: "Opened /mobile Web Phone in a new tab using active browser pairing.",
      });
      return;
    }
    try {
      const res = await fetch("/api/demo/pair-link", { method: "POST" });
      const body: unknown = await res.json();
      if (isRecord(body) && typeof body.pair === "string") {
        const target = `/mobile/?pair=${encodeURIComponent(body.pair)}#pair=${encodeURIComponent(body.pair)}`;
        window.open(target, "_blank", "noopener,noreferrer");
        setBanner({
          kind: "ok",
          text: "Opened and auto-paired /mobile Web Phone in a new tab.",
        });
        setTimeout(refresh, 800);
      }
    } catch {
      setBanner({ kind: "err", text: "Could not mint a /mobile pair link." });
    }
  };

  const openMonitorTab = async () => {
    try {
      const res = await fetch("/api/demo/monitor-link", { method: "POST" });
      const body: unknown = await res.json();
      if (isRecord(body) && typeof body.token === "string") {
        const target = `/monitor/?t=${encodeURIComponent(body.token)}#t=${encodeURIComponent(body.token)}`;
        window.open(target, "_blank", "noopener,noreferrer");
        return;
      }
    } catch {
      // Fallback to direct /monitor/ which auto-fetches its own token.
    }
    window.open("/monitor/", "_blank", "noopener,noreferrer");
  };

  const dialCustomer = async (loanId: string, customerName: string) => {
    setBusyLoanId(loanId);
    setBanner(null);
    try {
      const res = await fetch("/api/demo/start-call", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ loan_id: loanId }),
      });
      const body: unknown = await res.json();
      if (res.ok && isRecord(body) && body.ok === true) {
        setBanner({
          kind: "ok",
          text: `Ringing ${customerName} (${loanId}) on /mobile Web Phone now. Click Accept on the Web Phone to speak live.`,
        });
      } else {
        const errText = isRecord(body) ? str(body.error) : "Call could not be started.";
        setBanner({ kind: "warn", text: errText });
      }
      await refresh();
    } catch {
      setBanner({ kind: "err", text: "Network error while starting call." });
    } finally {
      setBusyLoanId(null);
    }
  };

  const changeLanguage = async (loanId: string, language: string) => {
    try {
      const res = await fetch("/api/demo/language", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ loan_id: loanId, language }),
      });
      if (res.ok) {
        setBanner({
          kind: "ok",
          text: `Updated ${loanId} call language to ${language}.`,
        });
        await refresh();
      }
    } catch {
      setBanner({ kind: "err", text: "Could not change language." });
    }
  };

  const resetDemo = async () => {
    try {
      const res = await fetch("/api/demo/reset", { method: "POST" });
      if (res.ok) {
        setBanner({
          kind: "ok",
          text: "Reset all 1,291 accounts and restored initial seeded outcomes.",
        });
        await refresh();
      }
    } catch {
      setBanner({ kind: "err", text: "Could not reset demo state." });
    }
  };

  return (
    <main className="cockpit" data-theme={theme}>
      <header className="cockpit-topbar">
        <div className="cockpit-brand">
          <span className="cockpit-crest" aria-hidden>
            CB
          </span>
          <div>
            <div className="cockpit-title-row">
              <span className="cockpit-bank">Cymbal Bank</span>
              <span className="cockpit-divider">/</span>
              <h1 className="cockpit-heading">Vernacular Collections Voice Agent Cockpit</h1>
            </div>
          </div>
        </div>

        <div className="cockpit-statuses">
          <span className={data?.phoneOnline ? "pill pill-ok" : "pill pill-warn"}>
            <span className="pill-dot" aria-hidden />
            {data?.phoneOnline ? "/mobile Phone Connected" : "Awaiting /mobile Receiver"}
          </span>
          <span className="cockpit-badge num">
            {data?.liveModel || "gemini-3.1-flash-live-preview"}
          </span>
          <span className="cockpit-badge">TELEPHONY: WEB-UI (NO TWILIO)</span>
          <button type="button" className="cockpit-btn cockpit-btn-ghost" onClick={resetDemo}>
            Reset Demo State
          </button>
          <button
            type="button"
            className="cockpit-btn cockpit-btn-ghost"
            onClick={toggleTheme}
            aria-label="Toggle color theme"
          >
            {theme === "light" ? "Dark Mode" : "Light Mode"}
          </button>
        </div>
      </header>

      <section className="cockpit-launchbar" aria-label="One-click presenter controls">
        <div className="launch-actions">
          <button type="button" className="cockpit-btn cockpit-btn-gold" onClick={openMobileTab}>
            1. Open &amp; Auto-Pair /mobile Web Phone (New Tab) ↗
          </button>
          <button
            type="button"
            className="cockpit-btn cockpit-btn-secondary"
            onClick={openMonitorTab}
          >
            2. Open /monitor Live Negotiation Ladder (New Tab) ↗
          </button>
          <button
            type="button"
            className="cockpit-btn cockpit-btn-ghost"
            onClick={() => setShowDock((v) => !v)}
          >
            {showDock ? "Hide Embedded Split Dock" : "Show Embedded Split Dock"}
          </button>
        </div>
        {data?.activeCallLoanId && (
          <div className="active-call-banner">
            <span className="pill pill-ok">
              <span className="pill-dot" aria-hidden />
              Active Call: {data.activeCallCustomer} ({data.activeCallLoanId})
            </span>
          </div>
        )}
      </section>

      {banner && (
        <output className={`cockpit-alert cockpit-alert-${banner.kind}`}>{banner.text}</output>
      )}

      {data && (
        <section className="cockpit-kpis" aria-label="Portfolio telemetry">
          <div className="kpi-card">
            <span className="kpi-label">Overdue Queue</span>
            <strong className="kpi-val num">
              {data.queueTotalAccounts.toLocaleString("en-IN")}
            </strong>
            <span className="kpi-meta num">{formatInr(data.queueTotalDueInr)} total due</span>
          </div>
          <div className="kpi-card">
            <span className="kpi-label">Total Committed Today</span>
            <strong className="kpi-val num">{formatInr(data.analysis.totalCommittedInr)}</strong>
            <span className="kpi-meta num">{data.analysis.committedPct}% of called due</span>
          </div>
          <div className="kpi-card">
            <span className="kpi-label">UPI Links Sent Today</span>
            <strong className="kpi-val num">{formatInr(data.analysis.linksSentTodayInr)}</strong>
            <span className="kpi-meta num">
              {data.analysis.partialAgreements} partial (2-month floor)
            </span>
          </div>
          <div className="kpi-card">
            <span className="kpi-label">Promised Within 7 Days</span>
            <strong className="kpi-val num">
              {formatInr(data.analysis.promisedWithin7DaysInr)}
            </strong>
            <span className="kpi-meta num">
              {data.analysis.calledAccounts} called · {data.analysis.escalations} escalated
            </span>
          </div>
        </section>
      )}

      {showDock && (
        <section className="cockpit-dock" aria-label="Embedded Web Phone and Live Call Monitor">
          <div className="dock-pane dock-pane-phone">
            <div className="dock-header">
              <span className="dock-title">Embedded /mobile Web Phone Receiver</span>
              <a href="/mobile/" target="_blank" rel="noreferrer" className="dock-popout">
                Pop out /mobile ↗
              </a>
            </div>
            <iframe
              src="/mobile/"
              title="Cymbal Bank Web Phone Receiver"
              className="dock-iframe dock-iframe-phone"
              allow="microphone; autoplay"
            />
          </div>
          <div className="dock-pane dock-pane-monitor">
            <div className="dock-header">
              <span className="dock-title">Embedded /monitor Live Negotiation Ladder</span>
              <a href="/monitor/" target="_blank" rel="noreferrer" className="dock-popout">
                Pop out /monitor ↗
              </a>
            </div>
            <iframe
              src="/monitor/"
              title="Cymbal Bank Live Call Monitor"
              className="dock-iframe dock-iframe-monitor"
            />
          </div>
        </section>
      )}

      <section className="cockpit-section" aria-label="Featured workshop scenarios">
        <div className="section-head">
          <h2>Featured Live Workshop Scenarios (A / B / C)</h2>
        </div>
        <div className="scenario-grid">
          {(data?.scenarios ?? []).map((sc) => {
            const acc = sc.account;
            const isCalling = busyLoanId === acc.loanId || data?.activeCallLoanId === acc.loanId;
            return (
              <article key={sc.loanId} className="scenario-card">
                <div className="scenario-top">
                  <span className="scenario-tag">{sc.title}</span>
                  <span className={`status-pill status-${statusTone(acc.status)}`}>
                    {humanize(acc.status)}
                  </span>
                </div>

                <div className="scenario-identity">
                  <h3>{acc.customerName}</h3>
                  <span className="scenario-meta num">
                    {acc.loanId} · {acc.city} · {acc.schemeName}
                  </span>
                </div>

                <dl className="scenario-numbers num">
                  <div>
                    <dt>Total Due</dt>
                    <dd>{formatInr(acc.dueAmountInr)}</dd>
                  </div>
                  <div>
                    <dt>2-Month Floor</dt>
                    <dd>{formatInr(sc.floorInr)}</dd>
                  </div>
                  <div>
                    <dt>Language</dt>
                    <dd>{acc.language}</dd>
                  </div>
                  <div>
                    <dt>Attempt</dt>
                    <dd>#{acc.callAttempt} of 3</dd>
                  </div>
                </dl>

                <div className="scenario-rungs">
                  <span className="rungs-label">Negotiation Rungs</span>
                  <div className="rungs-pills num">
                    {sc.rungs.map((r, idx) => (
                      <span
                        key={r.months}
                        className={
                          idx === sc.rungs.length - 1 && sc.rungs.length > 1
                            ? "rung-chip rung-chip-floor"
                            : "rung-chip"
                        }
                      >
                        {formatInr(r.amountInr)} ({r.months}m
                        {idx === sc.rungs.length - 1 && sc.rungs.length > 1 ? " floor" : ""})
                      </span>
                    ))}
                  </div>
                </div>

                <div className="scenario-script">
                  <span className="script-label">Guardrail: {sc.highlight}</span>
                  <p className="script-quote">{sc.sampleBorrowerUtterance}</p>
                </div>

                <div className="scenario-footer">
                  <button
                    type="button"
                    className="cockpit-btn cockpit-btn-dial"
                    disabled={!acc.callable || isCalling}
                    onClick={() => dialCustomer(acc.loanId, acc.customerName)}
                  >
                    {data?.activeCallLoanId === acc.loanId
                      ? "Call In Progress…"
                      : !acc.callable
                        ? `Completed (${humanize(acc.status)})`
                        : "Dial Customer Now"}
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      </section>

      <section className="cockpit-section" aria-label="Top 10 overdue borrower accounts">
        <div className="section-head">
          <h2>Priority Overdue Borrower Accounts (Top 10 Queue)</h2>
        </div>
        <div className="table-wrap">
          <table className="cockpit-table">
            <thead>
              <tr>
                <th>Borrower</th>
                <th>Loan ID</th>
                <th>City &amp; Branch</th>
                <th>Language</th>
                <th>Total Due</th>
                <th>2-Mo Floor</th>
                <th>Attempt</th>
                <th>Risk</th>
                <th>Status</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {(data?.queueTop ?? []).map((row) => {
                const isScenario =
                  row.loanId === "GL3506468235" ||
                  row.loanId === "GL0106397512" ||
                  row.loanId === "GL6095694164";
                return (
                  <tr key={row.loanId} className={isScenario ? "row-featured" : ""}>
                    <td>
                      <div className="cell-borrower">
                        <strong>{row.customerName}</strong>
                        <span className="cell-sub">{row.schemeName}</span>
                      </div>
                    </td>
                    <td className="num">{row.loanId}</td>
                    <td>
                      <div className="cell-borrower">
                        <span>{row.city}</span>
                        <span className="cell-sub">{row.branch}</span>
                      </div>
                    </td>
                    <td>
                      <select
                        className="lang-select"
                        aria-label={`Language for ${row.customerName}`}
                        value={row.language}
                        onChange={(e) => changeLanguage(row.loanId, e.target.value)}
                      >
                        {LANGUAGES.map((lang) => (
                          <option key={lang} value={lang}>
                            {lang}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="num cell-strong">{formatInr(row.dueAmountInr)}</td>
                    <td className="num">{formatInr(row.botAskAmountInr)}</td>
                    <td className="num">#{row.callAttempt}</td>
                    <td>
                      <span className={`risk-badge risk-${row.riskTier.toLowerCase()}`}>
                        {row.riskTier}
                      </span>
                    </td>
                    <td>
                      <span className={`status-pill status-${statusTone(row.status)}`}>
                        {humanize(row.status)}
                      </span>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="cockpit-btn cockpit-btn-sm"
                        disabled={!row.callable || busyLoanId === row.loanId}
                        onClick={() => dialCustomer(row.loanId, row.customerName)}
                      >
                        {data?.activeCallLoanId === row.loanId
                          ? "Ringing…"
                          : row.callable
                            ? "Dial Customer Now"
                            : "Handled"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {data && (data.handledToday.length > 0 || data.handoffs.length > 0) && (
        <section className="cockpit-bottom-grid" aria-label="Handled today and branch escalations">
          <div className="cockpit-panel">
            <h2>Accounts Handled Today ({data.handledToday.length})</h2>
            <ul className="handled-list">
              {data.handledToday.map((h) => (
                <li key={h.loanId} className="handled-item">
                  <div>
                    <strong>{h.customerName}</strong>{" "}
                    <span className="num muted">({h.loanId})</span>
                  </div>
                  <span className={`status-pill status-${statusTone(h.status)}`}>
                    {humanize(h.status)}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          <div className="cockpit-panel">
            <h2>Branch Manager Handoff Briefs ({data.handoffs.length})</h2>
            {data.handoffs.length === 0 ? (
              <p className="muted">
                Run Scenario C (Venkatesh Shetty) to generate a live branch escalation brief.
              </p>
            ) : (
              <ul className="handoff-list">
                {data.handoffs.map((b) => (
                  <li key={b.loanId} className="handoff-card">
                    <div className="handoff-top">
                      <strong>
                        {b.customerName} ({b.loanId})
                      </strong>
                      <span className="status-pill status-attention">{b.branch}</span>
                    </div>
                    <p className="handoff-summary">{b.customerSummary}</p>
                    <div className="handoff-meta num">
                      <span>Disputed Fee: {formatInr(b.disputedFeeInr)}</span>
                      <span>Callback Date: {b.branchCallbackDate}</span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      )}
    </main>
  );
}

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
