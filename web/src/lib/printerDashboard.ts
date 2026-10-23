/** Client-side classification, summary, filter and sort for the print dashboard (from `/printers/overview`). */
import type { PrinterOverviewItem } from "@/api/types";
import type { DashSort, DashStatus, PrintDashSearch } from "@/pages/printerDashSearch";

export function dashStatus(item: PrinterOverviewItem): DashStatus {
  const s = item.status;
  if (!s.online) return "offline";
  if (s.gcode_state === "FAILED" || (s.print_error ?? 0) !== 0) return "problem";
  if (s.gcode_state === "RUNNING" || s.gcode_state === "PREPARE") return "printing";
  if (s.gcode_state === "PAUSE") return "paused";
  if (s.gcode_state === "FINISH") return "finished";
  return "available";
}

export type DashCounts = Record<DashStatus, number>;

export interface DashSummary {
  counts: DashCounts;
  /** The printing/paused printer that frees up soonest. */
  next: { name: string; minutes: number } | null;
}

export function summarize(items: PrinterOverviewItem[]): DashSummary {
  const counts: DashCounts = { printing: 0, paused: 0, finished: 0, available: 0, problem: 0, offline: 0 };
  let next: DashSummary["next"] = null;
  for (const it of items) {
    const st = dashStatus(it);
    counts[st] += 1;
    const mins = it.status.mc_remaining_time;
    if ((st === "printing" || st === "paused") && mins != null && mins >= 0 && (!next || mins < next.minutes)) {
      next = { name: it.printer.name, minutes: mins };
    }
  }
  return { counts, next };
}

const STATUS_ORDER: DashStatus[] = ["printing", "paused", "problem", "finished", "available", "offline"];

export function filterItems(items: PrinterOverviewItem[], search: PrintDashSearch): PrinterOverviewItem[] {
  const q = (search.q ?? "").trim().toLowerCase();
  return items.filter((it) => {
    const st = dashStatus(it);
    if (search.status && st !== search.status) return false;
    if (search.offline === "hide" && st === "offline") return false;
    if (q && !`${it.printer.name} ${it.printer.model ?? ""}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

export interface DashGroup {
  key: string;
  label: string | null;
  items: PrinterOverviewItem[];
}

const byName = (a: PrinterOverviewItem, b: PrinterOverviewItem) =>
  a.printer.name.localeCompare(b.printer.name, undefined, { numeric: true });

const eta = (it: PrinterOverviewItem) => {
  const st = dashStatus(it);
  const m = it.status.mc_remaining_time;
  return (st === "printing" || st === "paused") && m != null ? m : Number.POSITIVE_INFINITY;
};

/** Sorted items, grouped under headers for the status / model sorts. */
export function sortGroups(items: PrinterOverviewItem[], sort: DashSort = "name"): DashGroup[] {
  if (sort === "status") {
    return STATUS_ORDER.map((st) => ({
      key: st,
      label: st[0].toUpperCase() + st.slice(1),
      items: items.filter((it) => dashStatus(it) === st).sort(byName),
    })).filter((g) => g.items.length > 0);
  }
  if (sort === "model") {
    const models = [...new Set(items.map((it) => it.printer.model ?? "Unknown model"))].sort((a, b) => a.localeCompare(b));
    return models.map((m) => ({
      key: m,
      label: m,
      items: items.filter((it) => (it.printer.model ?? "Unknown model") === m).sort(byName),
    }));
  }
  const sorted = [...items].sort(sort === "eta" ? (a, b) => eta(a) - eta(b) || byName(a, b) : byName);
  return [{ key: "all", label: null, items: sorted }];
}
