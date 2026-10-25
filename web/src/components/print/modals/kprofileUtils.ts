import i18n from "@/i18n";
import type { AmsTray, PrinterOut } from "@/api/types";

export const NOZZLES = ["0.2", "0.4", "0.6", "0.8"];

export function printerNozzle(printer: PrinterOut): string {
  const n = Number(printer.options?.nozzle_diameter);
  return Number.isFinite(n) && n > 0 ? n.toFixed(1) : "0.4";
}

/** Current nozzle first, then the rest. */
export function nozzleOrder(current: string): string[] {
  return [current, ...NOZZLES.filter((n) => n !== current)];
}

export function trayLabel(tr: AmsTray): string {
  return tr.ams_unit === 255 ? i18n.t("kprofile.ext") : `A${(tr.ams_unit ?? 0) + 1}-${(tr.tray ?? tr.slot) + 1}`;
}

/** Wire ids for select: external spools send tray 0/1 (254/255 -> 0/1; backend maps to 254 + n on dual-nozzle). */
export function selectIds(tr: AmsTray): { ams_id: number; tray_id: number } {
  const id = tr.tray ?? tr.slot;
  if (tr.ams_unit === 255) return { ams_id: 255, tray_id: id >= 254 ? id - 254 : id };
  return { ams_id: tr.ams_unit ?? 0, tray_id: id };
}

/** K 0-2 inclusive, n finite >= 0; returns an error key or null. */
export function validateK(k: string, n: string): "k" | "n" | null {
  const kv = Number(k);
  if (k.trim() === "" || !Number.isFinite(kv) || kv < 0 || kv > 2) return "k";
  const nv = Number(n);
  if (n.trim() === "" || !Number.isFinite(nv) || nv < 0 || nv > 2) return "n";
  return null;
}
