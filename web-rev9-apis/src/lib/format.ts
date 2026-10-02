const intFormat = new Intl.NumberFormat("en-US");

export function fmtInt(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return intFormat.format(Math.round(value));
}

/** Token limits: 200000 → 200K, 65536 → 64K, 1048576 → 1M. */
export function fmtTokens(value: number | null | undefined): string {
  if (!value) return "—";
  if (value >= 1_000_000) return `${+(value / 1_000_000).toFixed(1)}M`;
  if (value % 1024 === 0) return `${value / 1024}K`;
  return `${Math.round(value / 1000)}K`;
}

export function fmtCompact(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value >= 1_000_000_000) return `${+(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `${+(value / 1_000_000).toFixed(1)}M`;
  if (value >= 10_000) return `${+(value / 1_000).toFixed(1)}K`;
  return fmtInt(value);
}

export function fmtMs(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${fmtInt(value)} ms`;
}

export function fmtTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString("en-GB", { hour12: false });
}

export function fmtDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return `${date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })} ${date.toLocaleTimeString("en-GB", { hour12: false })}`;
}

export function fmtRelative(iso: string | null): string {
  if (!iso) return "never";
  const diff = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(diff)) return "—";
  const minutes = Math.round(diff / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export const STATUS_TEXT: Record<number, string> = {
  200: "OK",
  201: "Created",
  400: "Bad Request",
  401: "Unauthorized",
  403: "Forbidden",
  404: "Not Found",
  408: "Timeout",
  413: "Too Large",
  429: "Too Many Requests",
  500: "Internal Error",
  502: "Bad Gateway",
  503: "Unavailable",
  504: "Gateway Timeout",
};
