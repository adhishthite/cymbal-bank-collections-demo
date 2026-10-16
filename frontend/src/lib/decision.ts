import { isRecord, num, str } from "@/lib/storage";

export type Consent = { status: string; decision: string; amountInr: number | null };
export type TurnRow = {
  turnId: number;
  revision: number;
  status: string;
  act: string;
  actConfidence: number | null;
  offerInr: number | null;
  paymentTime: string;
  askInr: number | null;
  refusals: number | null;
  flags: string[];
  frustration: number | null;
  willingness: number | null;
  consent: (Consent & { revision: number }) | null;
};
export type Gate = { tool: string; allowed: boolean; code: string; unverified: boolean };
export type Audit = { turnId: number | null; flags: string[] };
// "samples" holds the request times for the median. After a seed it holds copies of the server median.
export type Timing = { n: number; max: number; samples: number[] };
export type Wait = { n: number; total: number; max: number };

export type DecisionFeed = {
  turns: TurnRow[];
  gates: Gate[];
  audits: Audit[];
  decisionMs: Timing;
  consentMs: Timing;
  wait: Wait;
};

export const EMPTY_DECISIONS: DecisionFeed = {
  turns: [],
  gates: [],
  audits: [],
  decisionMs: { n: 0, max: 0, samples: [] },
  consentMs: { n: 0, max: 0, samples: [] },
  wait: { n: 0, total: 0, max: 0 },
};

const MAX_TURNS = 12;
const MAX_EVENTS = 20;
const MAX_SAMPLES = 200;

const FLAG_LABELS: Record<string, string> = {
  WRONG_PERSON: "Wrong person",
  WANTS_HUMAN: "Wants a person",
  ALREADY_PAID: "Says already paid",
  REOPENS: "Wants to pay now",
  CLAIMED_LINK_WITHOUT_SEND: "Bot claimed a link it did not send",
  CLAIMED_RESEND_ON_DUPLICATE: "Bot claimed resend",
  CLAIMED_CALLBACK_WITHOUT_BOOKING: "Bot claimed a callback it did not book",
  CLAIMED_ESCALATION_WITHOUT_ACTION: "Bot claimed escalation without action",
  SYSTEM_TEXT: "System text spoken",
  UNSUPPORTED_PROMISE: "Bot made an unsupported promise",
};

const words = (v: string): string => v.replaceAll("_", " ").toLowerCase();

// Display only: a plain label for a flag code.
export function flagLabel(code: string): string {
  return FLAG_LABELS[code] ?? words(code).replace(/^\w/, (c) => c.toUpperCase());
}

// Borrower flags come from the answers of one decision event. The thresholds follow the design contract.
function borrowerFlags(m: Record<string, unknown>): string[] {
  const flags: string[] = [];
  const at = (key: string, min: number) => (num(m[key]) ?? 0) >= min;
  if (at("wrong_person", 0.9)) flags.push("WRONG_PERSON");
  if (at("wants_human", 0.8)) flags.push("WANTS_HUMAN");
  if (at("already_paid", 0.8)) flags.push("ALREADY_PAID");
  if (at("reopens", 0.8)) flags.push("REOPENS");
  const dispute = str(m.dispute);
  if (dispute && dispute !== "NONE") flags.push(`DISPUTE:${dispute}`);
  const hardship = str(m.hardship);
  if (hardship && hardship !== "NONE") flags.push(`HARDSHIP:${hardship}`);
  const lang = str(m.language_request);
  if (lang && lang !== "NONE") flags.push(`LANGUAGE:${lang}`);
  return flags;
}

// "DISPUTE:FEE_OR_PENALTY" -> "Dispute: fee or penalty". Other codes use the label table.
export function borrowerFlagLabel(flag: string): string {
  const [kind, value = ""] = flag.split(":");
  if (kind === "DISPUTE") return `Dispute: ${words(value)}`;
  if (kind === "HARDSHIP") return `Hardship: ${words(value)}`;
  if (kind === "LANGUAGE") return `Asks for ${words(value).replace(/^\w/, (c) => c.toUpperCase())}`;
  return flagLabel(flag);
}

const blank = (turnId: number): TurnRow => ({
  turnId,
  revision: -1,
  status: "unavailable",
  act: "",
  actConfidence: null,
  offerInr: null,
  paymentTime: "",
  askInr: null,
  refusals: null,
  flags: [],
  frustration: null,
  willingness: null,
  consent: null,
});

function upsert(turns: TurnRow[], row: TurnRow): TurnRow[] {
  const rest = turns.filter((t) => t.turnId !== row.turnId);
  return [...rest, row].sort((a, b) => a.turnId - b.turnId).slice(-MAX_TURNS);
}

function addSample(t: Timing, ms: number | null): Timing {
  if (ms === null) return t;
  return { n: t.n + 1, max: Math.max(t.max, ms), samples: [...t.samples, ms].slice(-MAX_SAMPLES) };
}

// The decision and the consent each read one revision of the borrower turn. The older one is stale and
// the panel must not show its value as current. A turn with only one of them has no stale part.
export function staleParts(t: TurnRow): { decision: boolean; consent: boolean } {
  if (!t.consent) return { decision: false, consent: false };
  return { decision: t.revision < t.consent.revision, consent: t.consent.revision < t.revision };
}

export function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const s = [...values].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

// Applies one decision, consent, gate or audit event. Other event types change nothing.
export function applyDecisionEvent(d: DecisionFeed, msg: Record<string, unknown>): DecisionFeed {
  const type = str(msg.type);
  const turnId = num(msg.turn_id);
  const revision = num(msg.revision) ?? 0;
  if (type === "decision" && turnId !== null) {
    const old = d.turns.find((t) => t.turnId === turnId) ?? blank(turnId);
    // The backend leaves unavailable results out of the latency stats. Match it.
    const decisionMs = addSample(d.decisionMs, msg.status === "ready" ? num(msg.latency_ms) : null);
    if (revision < old.revision) return { ...d, decisionMs };
    const ready = msg.status === "ready";
    const row: TurnRow = {
      ...old,
      revision,
      status: ready ? "ready" : "unavailable",
      act: ready ? str(msg.act) : "",
      actConfidence: ready ? num(msg.act_confidence) : null,
      offerInr: ready ? num(msg.offer_inr) : null,
      paymentTime: ready ? str(msg.payment_time) : "",
      askInr: num(msg.current_ask_inr),
      refusals: ready ? num(msg.refusals) : null,
      flags: ready ? borrowerFlags(msg) : [],
      frustration: ready ? num(msg.frustration) : null,
      willingness: ready ? num(msg.willingness) : null,
    };
    return { ...d, turns: upsert(d.turns, row), decisionMs };
  }
  if (type === "consent" && turnId !== null) {
    const old = d.turns.find((t) => t.turnId === turnId) ?? blank(turnId);
    const consentMs = addSample(d.consentMs, msg.status === "ready" ? num(msg.latency_ms) : null);
    if (old.consent && revision < old.consent.revision) return { ...d, consentMs };
    const ready = msg.status === "ready";
    const consent = {
      revision,
      status: ready ? "ready" : "unavailable",
      decision: ready ? str(msg.decision) : "",
      amountInr: ready ? num(msg.amount_inr) : null,
    };
    return { ...d, turns: upsert(d.turns, { ...old, consent }), consentMs };
  }
  if (type === "gate") {
    const gate: Gate = {
      tool: str(msg.tool),
      allowed: msg.allowed === true,
      code: str(msg.code),
      unverified: msg.unverified === true,
    };
    return { ...d, gates: [...d.gates, gate].slice(-MAX_EVENTS) };
  }
  if (type === "audit") {
    const flags = Array.isArray(msg.flags) ? msg.flags.map(str) : [];
    const audit: Audit = { turnId, flags };
    return { ...d, audits: [...d.audits, audit].slice(-MAX_EVENTS) };
  }
  if (type === "tool") {
    const ms = num(msg.decision_wait_ms);
    if (ms === null) return d;
    return {
      ...d,
      wait: { n: d.wait.n + 1, total: d.wait.total + ms, max: Math.max(d.wait.max, ms) },
    };
  }
  return d;
}

function seedTiming(raw: unknown): Timing {
  if (!isRecord(raw)) return EMPTY_DECISIONS.decisionMs;
  const n = num(raw.n) ?? 0;
  const med = num(raw.median_ms);
  const samples = med === null ? [] : Array<number>(Math.min(n, MAX_SAMPLES)).fill(med);
  return { n, max: num(raw.max_ms) ?? 0, samples };
}

// Builds the panel from the "decisions" part of a state event, so a reconnect shows the panel at once.
// The part holds lists of events and a latency summary. A missing or odd part gives an empty panel.
export function seedDecisions(raw: unknown): DecisionFeed {
  if (!isRecord(raw)) return EMPTY_DECISIONS;
  let d = EMPTY_DECISIONS;
  for (const list of Object.values(raw)) {
    if (!Array.isArray(list)) continue;
    for (const ev of list) {
      if (!isRecord(ev)) continue;
      // `turns` items wrap the latest decision and consent events of one turn.
      const wrapped = [ev.decision, ev.consent].filter(isRecord);
      for (const e of ev.type ? [ev] : wrapped) d = applyDecisionEvent(d, e);
    }
  }
  const lat = isRecord(raw.latency) ? raw.latency : {};
  const w = isRecord(lat.tool_wait) ? lat.tool_wait : {};
  return {
    ...d,
    decisionMs: seedTiming(lat.decision),
    consentMs: seedTiming(lat.consent),
    wait: { n: num(w.n) ?? 0, total: num(w.total_ms) ?? 0, max: num(w.max_ms) ?? 0 },
  };
}

// Display only: one log line for a gate event.
export function gateLabel(g: Gate): string {
  if (!g.allowed) {
    if (g.code === "CONSENT_REQUIRED") return "Link blocked: no clear yes";
    if (g.code === "REFUSALS_REQUIRED") return "Callback blocked: fewer than 2 refusals";
    if (g.code === "PROMISE_REQUIRED") return "Callback blocked: no promised date";
    if (g.code === "WRONG_PERSON") return "Blocked: wrong person";
    return `Blocked: ${words(g.code) || "unknown reason"}`;
  }
  return g.unverified ? "Allowed (unverified)" : "Allowed (verified)";
}
