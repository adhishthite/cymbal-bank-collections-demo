// The ask ladder on /monitor: the Asks of the call plan, from the full due amount down to the floor.

export type Rung = { months: number; amountInr: number };
export type LadderRow = {
  amountInr: number;
  // Null for a borrower offer that is not a rung of the plan.
  months: number | null;
  state: "passed" | "current" | "ahead" | "agreed" | "offer";
};

// `asks` are the Ask amounts of the borrower turns, oldest first. The bot only goes down the ladder,
// so the lowest rung that it asked is where it stands. A sent link marks its amount as agreed.
export function ladderRows(
  rungs: Rung[],
  asks: (number | null)[],
  offerInr: number | null,
  linkInr: number | null,
): LadderRow[] {
  let reached = -1;
  for (const ask of asks) {
    const i = rungs.findIndex((r) => r.amountInr === ask);
    if (i > reached) reached = i;
  }
  const rows: LadderRow[] = rungs.map((r, i) => ({
    amountInr: r.amountInr,
    months: r.months,
    state:
      linkInr === r.amountInr
        ? "agreed"
        : linkInr !== null
          ? i <= reached && r.amountInr > linkInr
            ? "passed"
            : "ahead"
          : i < reached
            ? "passed"
            : i === reached
              ? "current"
              : "ahead",
  }));
  // An amount that is not a rung goes in its place by size: the link, else the open offer.
  const extra = linkInr ?? offerInr;
  if (extra !== null && !rungs.some((r) => r.amountInr === extra)) {
    rows.push({ amountInr: extra, months: null, state: linkInr !== null ? "agreed" : "offer" });
    rows.sort((a, b) => b.amountInr - a.amountInr);
  }
  return rows;
}
