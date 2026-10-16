import { expect, test } from "bun:test";
import {
  EMPTY_DECISIONS,
  applyDecisionEvent,
  borrowerFlagLabel,
  median,
  seedDecisions,
  staleParts,
} from "./decision";

test("events fill the turn rows and the latency strip, and a reconnect seeds the same view", () => {
  let d = EMPTY_DECISIONS;
  d = applyDecisionEvent(d, {
    type: "decision",
    turn_id: 1,
    revision: 1,
    status: "ready",
    act: "REFUSE",
    act_confidence: 0.9,
    dispute: "FEE_OR_PENALTY",
    wrong_person: 0.02,
    current_ask_inr: 3274,
    refusals: 1,
    latency_ms: 300,
  });
  d = applyDecisionEvent(d, {
    type: "decision",
    turn_id: 2,
    revision: 1,
    status: "unavailable",
    latency_ms: 800,
  });
  d = applyDecisionEvent(d, {
    type: "consent",
    turn_id: 1,
    revision: 1,
    status: "ready",
    decision: "REFUSE",
    latency_ms: 1000,
  });
  d = applyDecisionEvent(d, { type: "tool", decision_wait_ms: 120 });
  expect(d.turns.map((t) => [t.turnId, t.status, t.act])).toEqual([
    [1, "ready", "REFUSE"],
    [2, "unavailable", ""],
  ]);
  expect(d.turns[0].flags.map(borrowerFlagLabel)).toEqual(["Dispute: fee or penalty"]);
  expect(median(d.decisionMs.samples)).toBe(300);
  expect(d.consentMs.max).toBe(1000);
  expect(d.wait).toEqual({ n: 1, total: 120, max: 120 });

  const seeded = seedDecisions({
    turns: [
      {
        turn_id: 5,
        decision: { type: "decision", turn_id: 5, revision: 1, status: "ready", act: "ACCEPT" },
        consent: null,
      },
    ],
    events: [{ type: "gate", tool: "generate_upi_payment_link", allowed: true, code: null }],
    latency: {
      decision: { n: 4, median_ms: 300, max_ms: 500 },
      tool_wait: { n: 2, total_ms: 90, max_ms: 60 },
    },
  });
  expect(seeded.turns[0].act).toBe("ACCEPT");
  expect(median(seeded.decisionMs.samples)).toBe(300);
  expect(seeded.decisionMs.n).toBe(4);
  expect(seedDecisions("junk")).toBe(EMPTY_DECISIONS);
});

test("an older decision or consent shows as stale when the other one reads a newer revision", () => {
  let d = applyDecisionEvent(EMPTY_DECISIONS, {
    type: "decision",
    turn_id: 1,
    revision: 1,
    status: "ready",
    act: "REFUSE",
  });
  d = applyDecisionEvent(d, {
    type: "consent",
    turn_id: 1,
    revision: 2,
    status: "ready",
    decision: "AGREE_TODAY",
  });
  expect(staleParts(d.turns[0])).toEqual({ decision: true, consent: false });
  d = applyDecisionEvent(d, {
    type: "decision",
    turn_id: 1,
    revision: 3,
    status: "ready",
    act: "ACCEPT",
  });
  expect(staleParts(d.turns[0])).toEqual({ decision: false, consent: true });
});
