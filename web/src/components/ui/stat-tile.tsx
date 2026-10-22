import * as React from "react"

import { cn } from "@/lib/utils"

/** A single dashboard metric (P3 Task 4 density spec): a label-caps
 * caption, a large tabular value with an optional muted unit and delta,
 * and room for a `children` visual (a `Sparkline`) underneath. Deliberately
 * plain -- no card chrome of its own; callers that want a boxed tile wrap
 * it in `<div className="border rounded-[6px] p-3">` themselves so a tile
 * can also sit bare inside a table cell. */
function StatTile({
  label,
  value,
  unit,
  delta,
  children,
  className,
  ...props
}: React.ComponentProps<"div"> & {
  label: React.ReactNode
  value: React.ReactNode
  unit?: React.ReactNode
  delta?: React.ReactNode
}) {
  return (
    <div data-slot="stat-tile" className={cn("flex flex-col gap-1", className)} {...props}>
      <span className="label-caps">{label}</span>
      <div className="flex items-baseline gap-1.5">
        <span className="font-mono text-2xl font-medium tabular-nums">{value}</span>
        {unit != null && <span className="text-sm text-muted-foreground">{unit}</span>}
        {delta != null && <span className="text-xs text-muted-foreground">{delta}</span>}
      </div>
      {children}
    </div>
  )
}

export { StatTile }
