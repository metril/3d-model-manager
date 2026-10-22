import * as React from "react"

import { cn } from "@/lib/utils"
import type { StateTone } from "@/lib/printerState"

const STROKE_BY_TONE: Record<StateTone | "primary", string> = {
  primary: "var(--color-primary)",
  printing: "var(--color-state-printing)",
  finished: "var(--color-state-finished)",
  failed: "var(--color-state-failed)",
  paused: "var(--color-state-paused)",
  idle: "var(--color-state-idle)",
  offline: "var(--color-state-offline)",
}

/** A tiny inline trend line (P3 Task 4): no axes, no gridlines, just a
 * polyline normalised into `[0, height]` -- `values[i]` maps linearly
 * between the series' own min (bottom) and max (top). Fewer than 2 points
 * can't describe a trend, so it renders a flat horizontal line instead of
 * a degenerate/empty shape. */
function Sparkline({
  values,
  width = 96,
  height = 24,
  tone = "primary",
  className,
  "aria-label": ariaLabel,
}: {
  values: number[]
  width?: number
  height?: number
  tone?: StateTone | "primary"
  className?: string
  "aria-label": string
}) {
  const points = React.useMemo(() => {
    if (values.length < 2) {
      const y = height / 2
      return `0,${y} ${width},${y}`
    }
    const min = Math.min(...values)
    const max = Math.max(...values)
    const span = max - min || 1
    const step = width / (values.length - 1)
    return values
      .map((v, i) => {
        const x = i * step
        const y = height - ((v - min) / span) * height
        return `${x},${y}`
      })
      .join(" ")
  }, [values, width, height])

  return (
    <svg
      data-slot="sparkline"
      role="img"
      aria-label={ariaLabel}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn("overflow-visible", className)}
    >
      <polyline
        points={points}
        fill="none"
        stroke={STROKE_BY_TONE[tone]}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  )
}

export { Sparkline }
