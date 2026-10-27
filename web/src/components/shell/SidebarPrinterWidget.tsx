import { Link } from "@tanstack/react-router";
import { Printer as PrinterIcon } from "lucide-react";

import { useFeatures } from "@/api/features";
import { usePrinterStatus, usePrinters } from "@/api/printers";
import { toneForGcode, type StateTone } from "@/lib/printerState";
import { cn } from "@/lib/utils";

// Mirrors `StateBadge`'s own tone->dot mapping (`ui/state-badge.tsx`) so
// this compact widget's dot reads as the same color vocabulary as the
// overview dashboard's badges, without pulling in that component's
// label-caps text styling, which doesn't fit this row's name+status layout.
const DOT_CLASS_BY_TONE: Record<StateTone, string> = {
  printing: "bg-state-printing",
  finished: "bg-state-finished",
  failed: "bg-state-failed",
  paused: "bg-state-paused",
  idle: "bg-state-idle",
  offline: "bg-state-offline",
};

/** Compact live-status footer widget (P3 Task 6): the first configured
 * printer's coarse state, reusing the same `usePrinters`/`usePrinterStatus`
 * hooks as `PrinterStatusPanel` so this never drifts from the real
 * per-printer detail page -- and the same `toneForGcode` tone vocabulary as
 * the overview dashboard's `StateBadge`s. Links straight to that printer's
 * `/printers/$id`; a `+N` suffix hints at the rest without listing them --
 * this stays a one-line widget no matter how many printers are configured.
 * Hidden when the feature flag is off or nothing is configured yet --
 * there's nothing useful to show either way. */
export function SidebarPrinterWidget({ collapsed }: { collapsed: boolean }) {
  const features = useFeatures();
  const enabled = !!features.data?.printer_enabled;
  const printers = usePrinters({ enabled });
  const printer = printers.data?.[0];
  const extraCount = Math.max((printers.data?.length ?? 0) - 1, 0);
  const status = usePrinterStatus(printer?.id ?? -1, { enabled: printer !== undefined });

  if (!enabled || !printer) return null;

  const s = status.data;
  const online = !!s?.online;
  const { tone, label } = toneForGcode(online, s?.gcode_state ?? null);

  return (
    <Link
      to="/printers/$id"
      params={{ id: String(printer.id) }}
      className={cn(
        "flex items-center gap-2 rounded-lg border border-border bg-muted/50 px-2.5 py-2 text-xs hover:bg-muted",
        collapsed && "justify-center px-0",
      )}
      title={collapsed ? printer.name : undefined}
    >
      {collapsed ? (
        <PrinterIcon
          className={cn("size-4 shrink-0", online ? "text-primary" : "text-muted-foreground/50")}
        />
      ) : (
        <>
          <span
            className={cn("size-2 shrink-0 rounded-full", DOT_CLASS_BY_TONE[tone], tone === "printing" && "animate-pulse")}
            aria-hidden
          />
          <span className="flex min-w-0 flex-1 items-center justify-between gap-1.5">
            <span className="truncate text-foreground">
              {printer.name}
              {extraCount > 0 && <span className="text-muted-foreground"> +{extraCount}</span>}
            </span>
            <span className="shrink-0 tabular-mono text-muted-foreground">
              {tone === "printing" ? `${s?.mc_percent ?? 0}%` : label}
            </span>
          </span>
        </>
      )}
    </Link>
  );
}
