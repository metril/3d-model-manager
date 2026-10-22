import * as React from "react"

import { cn } from "@/lib/utils"
import type { StateTone } from "@/lib/printerState"

const DOT_CLASS_BY_TONE: Record<StateTone, string> = {
  printing: "bg-state-printing",
  finished: "bg-state-finished",
  failed: "bg-state-failed",
  paused: "bg-state-paused",
  idle: "bg-state-idle",
  offline: "bg-state-offline",
}

/** A dot + label-caps text state indicator (P3 Task 4): the overview's
 * compact alternative to a full `Badge` pill, colored from the shared
 * `--state-*` tokens (`lib/printerState.ts`'s `StateTone`). `pulse` marks
 * the live "printing right now" row so it reads as active, not just
 * colored. */
function StateBadge({
  tone,
  label,
  pulse = false,
  className,
  ...props
}: React.ComponentProps<"span"> & { tone: StateTone; label: string; pulse?: boolean }) {
  return (
    <span
      data-slot="state-badge"
      className={cn("inline-flex items-center gap-1.5", className)}
      {...props}
    >
      <span
        aria-hidden="true"
        data-slot="state-badge-dot"
        className={cn("size-1.5 shrink-0 rounded-full", DOT_CLASS_BY_TONE[tone], pulse && "animate-pulse")}
      />
      <span className="label-caps">{label}</span>
    </span>
  )
}

export { StateBadge }
