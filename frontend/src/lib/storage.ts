// Storage can throw (private mode, blocked site data). The pages must work without it.
export function readStore(store: "local" | "session", key: string): string | null {
  try {
    return (store === "local" ? localStorage : sessionStorage).getItem(key);
  } catch {
    return null;
  }
}

export function writeStore(store: "local" | "session", key: string, value: string | null): void {
  try {
    const s = store === "local" ? localStorage : sessionStorage;
    if (value === null) s.removeItem(key);
    else s.setItem(key, value);
  } catch {
    // The value then lives only in memory for this page load.
  }
}

export const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

// Parses one JSON message that has a string "type". Returns null for anything else.
export function parseMessage(text: string): (Record<string, unknown> & { type: string }) | null {
  try {
    const v: unknown = JSON.parse(text);
    return isRecord(v) && typeof v.type === "string"
      ? (v as Record<string, unknown> & { type: string })
      : null;
  } catch {
    return null;
  }
}

export const str = (v: unknown): string => (typeof v === "string" ? v : v == null ? "" : String(v));
export const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;
