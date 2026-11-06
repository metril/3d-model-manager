/**
 * Small formatting helpers used instead of moment/lodash (Task 8
 * constraint: keep bundle sane — use `Intl` + small helpers).
 */
import { useEffect, useState } from "react";

import i18n from "@/i18n";

const SIZE_UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

/** Humanize a byte count, e.g. `1536` -> `"1.5 KB"`. Rounds before choosing
 * the unit, so 1023.99 KB reads "1.0 MB", never "1024 KB". */
export function humanizeBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;

  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < SIZE_UNITS.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  let rounded = Number(value.toFixed(value < 10 ? 1 : 0));
  if (rounded >= 1024 && unitIndex < SIZE_UNITS.length - 1) {
    rounded = 1;
    unitIndex += 1;
  }
  const precision = rounded < 10 ? 1 : 0;
  return `${rounded.toFixed(precision)} ${SIZE_UNITS[unitIndex]}`;
}

const fmtCache = new Map<string, Intl.DateTimeFormat>();
function cachedDateFormat(key: string, opts: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const id = `${i18n.language}|${key}`;
  let f = fmtCache.get(id);
  if (!f) {
    f = new Intl.DateTimeFormat(i18n.language, opts);
    fmtCache.set(id, f);
  }
  return f;
}

/** Format an ISO datetime string for display, e.g. "Jul 5, 2026, 3:04 PM". */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return cachedDateFormat("dt", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

/** Format an ISO datetime string as a date only, e.g. "Jul 5, 2026". */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return cachedDateFormat("d", { dateStyle: "medium" }).format(date);
}

/** Format a `Date` as the local-time value an `<input type="datetime-local">`
 * both reads and emits, e.g. `"2026-07-11T14:05"` -- no timezone, no
 * seconds. Round-trips through `new Date(value)`, which per the Date Time
 * String spec treats a timezone-less date-*time* form (unlike a date-only
 * form) as local time, matching what the control itself means. */
export function toDatetimeLocalValue(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** Humanize a duration in seconds, e.g. `5400` -> `"1h 30m"`, `2700` ->
 * `"45m"`, anything under a minute -> `"<1m"`. */
export function humanizeDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 60) return "<1m";
  const totalMinutes = Math.round(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}

/** Format an ISO datetime string as a short relative time, e.g. "5 minutes
 * ago", "yesterday" -- falls back to `formatDate` past 30 days, since a
 * "47 days ago" reads worse than an absolute date at that distance. */
export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";

  const diffSeconds = Math.round((date.getTime() - Date.now()) / 1000);
  const abs = Math.abs(diffSeconds);

  const relativeTimeFormatter = new Intl.RelativeTimeFormat(i18n.language, { numeric: "auto" });
  if (abs < 60) return i18n.t("format.justNow");
  if (abs < 3600) return relativeTimeFormatter.format(Math.round(diffSeconds / 60), "minute");
  if (abs < 86400) return relativeTimeFormatter.format(Math.round(diffSeconds / 3600), "hour");
  if (abs < 30 * 86400) return relativeTimeFormatter.format(Math.round(diffSeconds / 86400), "day");
  return formatDate(iso);
}

/** Debounce a fast-changing value; returns the value after `delayMs` of quiet. */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);

  return debounced;
}

let currencyCode = "";

/** ISO 4217 code from the server's `currency` setting; empty = no symbol. */
export function setCurrency(code: string | null | undefined): void {
  currencyCode = (code ?? "").trim().toUpperCase();
}

const costFmtCache = new Map<string, Intl.NumberFormat>();
function costFormat(): Intl.NumberFormat {
  const key = `${i18n.language}|${currencyCode}`;
  let f = costFmtCache.get(key);
  if (!f) {
    const base = { minimumFractionDigits: 2, maximumFractionDigits: 2 };
    try {
      // Currency mode: let Intl pick the currency's own fraction digits (JPY 0, KWD 3).
      f = new Intl.NumberFormat(i18n.language, currencyCode ? { style: "currency", currency: currencyCode } : base);
    } catch {
      f = new Intl.NumberFormat(i18n.language, base); // invalid code -> plain number
    }
    costFmtCache.set(key, f);
  }
  return f;
}

/** Run cost, 2 decimals (with a currency symbol when the server's `currency`
 * is set); `--` when unknown. A negative zero never prints as "-0.00". */
export function formatCost(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "--";
  const fmt = costFormat();
  const digits = fmt.resolvedOptions().maximumFractionDigits ?? 2;
  const m = 10 ** digits;
  const rounded = Math.round(value * m) / m;
  return fmt.format(rounded === 0 ? 0 : rounded);
}
