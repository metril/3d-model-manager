import { useEffect } from "react";

import type { PrinterOverviewItem } from "@/api/types";
import { formatEta } from "@/lib/formatEta";

const APP_TITLE = "3D Model Manager";

/** Sets `document.title` while `title` is non-null; restores the previous
 * title on change/unmount. */
export function useDocumentTitle(title: string | null): void {
  useEffect(() => {
    if (!title) return;
    const prev = document.title;
    document.title = title;
    return () => {
      document.title = prev;
    };
  }, [title]);
}

/** `"1h 23m · <printer> · 3D Model Manager"` for the soonest-finishing
 * running print, or null when nothing is printing. */
export function printTitle(items: readonly PrinterOverviewItem[]): string | null {
  let best: { name: string; mins: number } | null = null;
  for (const it of items) {
    const mins = it.status.mc_remaining_time;
    if (it.status.gcode_state !== "RUNNING" || mins == null || formatEta(mins) == null) continue;
    if (!best || mins < best.mins) best = { name: it.printer.name, mins };
  }
  return best ? `${formatEta(best.mins)} · ${best.name} · ${APP_TITLE}` : null;
}

/** Tab title showing remaining print time for the given printers. */
export function usePrintTitle(items: readonly PrinterOverviewItem[]): void {
  useDocumentTitle(printTitle(items));
}
