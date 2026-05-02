// Formatting helpers shared across pages. Times render in two flavours:
//   * absolute (UTC ISO) — for tooltips
//   * relative ("3h ago", "yesterday") — for display
// Numbers use locale-grouping. Bytes/percentages stay plain.

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return "—";
  const seconds = (Date.now() - dt.getTime()) / 1000;
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 86400 * 7) return `${Math.floor(seconds / 86400)}d ago`;
  return dt.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function uptime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  const d = Math.floor(h / 24);
  return `${d}d ${h % 24}h`;
}

export function formatNumber(n: number): string {
  return n.toLocaleString();
}

// Binary-prefix bytes formatter. 1024-based, one decimal, terse units.
// Used for the browser-tab profile size and screenshot tile metadata —
// kept here next to the other formatters so it stays one import away.
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  // One decimal up through GB; drop the decimal once values get large.
  const precision = value >= 100 ? 0 : 1;
  return `${value.toFixed(precision)} ${units[unit]}`;
}

export function clampPercent(n: number): number {
  return Math.max(0, Math.min(100, Math.round(n)));
}

export function classNames(
  ...parts: (string | false | null | undefined)[]
): string {
  return parts.filter(Boolean).join(" ");
}

// Human label for a curator-run folder name (e.g. "2026-05-01T184211Z").
export function prettyRunFolder(folder: string): string {
  const m = folder.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2})(\d{2})(\d{2})Z$/);
  if (!m) return folder;
  const [, y, mo, d, h, mi] = m;
  return `${y}-${mo}-${d} · ${h}:${mi} UTC`;
}
