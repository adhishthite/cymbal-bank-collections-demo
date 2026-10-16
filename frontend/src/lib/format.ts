const inr = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

// en-IN gives the Indian grouping: 8612 -> ₹8,612 and 123456 -> ₹1,23,456.
export const formatInr = (amount: number): string => inr.format(amount);

const day = new Intl.DateTimeFormat("en-IN", {
  weekday: "short",
  day: "numeric",
  month: "short",
  timeZone: "UTC",
});

// "2026-10-09" -> "Fri, 9 Oct". The date is a calendar date: format it in UTC so that no
// timezone moves it to another day. Other text comes back unchanged.
export function formatDate(value: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  return m ? day.format(new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]))) : value;
}

export const formatClock = (seconds: number): string =>
  `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;

// Same-origin socket URL: wss on https, ws on http.
export function wsUrl(path: string): string {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}${path}`;
}

// Reads one key from the URL fragment or query string and removes it from the address bar,
// so the token does not stay in the history or show on a projected screen.
export function takeFragmentParam(key: string): string | null {
  const hashVal = location.hash ? new URLSearchParams(location.hash.slice(1)).get(key) : null;
  const searchVal = location.search ? new URLSearchParams(location.search.slice(1)).get(key) : null;
  const value = hashVal ?? searchVal;
  if (value !== null || location.hash) {
    history.replaceState(null, "", location.pathname);
  }
  return value;
}

// Reconnect delay: 1, 2, 4 ... s, up to 30 s.
export const backoffMs = (attempt: number): number => Math.min(30_000, 1000 * 2 ** attempt);

export const humanize = (value: string): string =>
  value
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/^\w/, (c) => c.toUpperCase());
