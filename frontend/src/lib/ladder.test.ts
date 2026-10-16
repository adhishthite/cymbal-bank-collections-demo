import { expect, test } from "bun:test";
import { ladderRows } from "@/lib/ladder";

// Sajeev George: ₹8,185 (5 months), ₹4,911 (3 months), ₹3,274 (2 months, the floor).
const SAJEEV = [
  { months: 5, amountInr: 8185 },
  { months: 3, amountInr: 4911 },
  { months: 2, amountInr: 3274 },
];
const states = (rows: ReturnType<typeof ladderRows>) =>
  rows.map((r) => `${r.amountInr}:${r.state}`);

test("the bot walks down the ladder and the borrower agrees to the floor", () => {
  expect(states(ladderRows(SAJEEV, [8185], null, null))).toEqual([
    "8185:current",
    "4911:ahead",
    "3274:ahead",
  ]);
  // The ₹1,637 counter-offer is below the floor. It shows in its place as an open offer.
  expect(states(ladderRows(SAJEEV, [8185, 4911], 1637, null))).toEqual([
    "8185:passed",
    "4911:current",
    "3274:ahead",
    "1637:offer",
  ]);
  expect(states(ladderRows(SAJEEV, [8185, 4911, 3274, null], 3274, 3274))).toEqual([
    "8185:passed",
    "4911:passed",
    "3274:agreed",
  ]);
});

test("a link for the borrower's own amount shows between the rungs", () => {
  expect(states(ladderRows(SAJEEV, [8185, 4911], 4000, 4000))).toEqual([
    "8185:passed",
    "4911:passed",
    "4000:agreed",
    "3274:ahead",
  ]);
});
