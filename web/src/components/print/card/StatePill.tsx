import { cn } from "@/lib/utils";
import type { StateTone } from "@/lib/printerState";

import { pillStyle, toneVar } from "./tone";

/** The one status indicator on a card: tinted pill, pulse dot only while printing. */
export function StatePill({ tone, label, className }: { tone: StateTone; label: string; className?: string }) {
  return (
    <span
      data-slot="state-pill"
      data-tone={tone}
      className={cn("pc-t11 inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 font-medium", className)}
      style={pillStyle(tone)}
    >
      <span
        aria-hidden="true"
        className={cn("size-1.5 rounded-full", tone === "printing" && "animate-pulse")}
        style={{ background: toneVar(tone) }}
      />
      {label}
    </span>
  );
}
