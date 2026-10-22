import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"

/** Normalize a hex color to `#RRGGBB`. Accepts `#RRGGBB`, bare `RRGGBB`, and
 * 8-digit `RRGGBBAA` / `#RRGGBBAA` (the printer's AMS `tray_color` carries a
 * trailing alpha) — the alpha is stripped. Returns `null` for empty or
 * unparseable input so callers render a placeholder instead of emitting an
 * invalid inline style. */
export function normalizeHex(input?: string | null): string | null {
  if (!input) return null
  const hex = input.trim().replace(/^#/, "")
  if (!/^[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$/.test(hex)) return null
  return `#${hex.slice(0, 6)}`
}

const filamentChipVariants = cva("inline-flex items-center gap-1.5 align-middle", {
  variants: {
    size: {
      sm: "text-xs",
      md: "text-sm",
    },
  },
  defaultVariants: { size: "sm" },
})

const SWATCH_SIZE = { sm: "size-3", md: "size-4" } as const

/** The redesign's signature motif: a rounded filament-color swatch, reused for
 * AMS slots, tags, and the viewer's per-part recolor control. */
function FilamentChip({
  color,
  label,
  material,
  lowStock = false,
  remainPct,
  size = "sm",
  className,
  ...props
}: React.ComponentProps<"span"> &
  VariantProps<typeof filamentChipVariants> & {
    color?: string | null
    label?: string
    material?: string
    /** Muted "Low" badge (never red/amber -- that vocabulary is reserved
     * for primary/in-progress state, P5 Task 10). */
    lowStock?: boolean
    /** The printer's own remaining-material estimate, rendered as a small
     * muted tabular-nums tick next to the swatch. `null`/`undefined` (AMS
     * doesn't report it, or the slot is empty) renders nothing. */
    remainPct?: number | null
  }) {
  const hex = normalizeHex(color)
  const description = [label, material, hex ?? "no color", lowStock ? "low stock" : null]
    .filter(Boolean)
    .join(" · ")
  const swatchClass = SWATCH_SIZE[size ?? "sm"]

  return (
    <span
      data-slot="filament-chip"
      className={cn(filamentChipVariants({ size }), className)}
      {...props}
    >
      {hex ? (
        <span
          aria-hidden="true"
          title={description}
          className={cn(swatchClass, "shrink-0 rounded-full border border-border/60")}
          style={{ backgroundColor: hex }}
        />
      ) : (
        <span
          aria-hidden="true"
          title={description}
          className={cn(
            swatchClass,
            "shrink-0 rounded-full border border-dashed border-muted-foreground/40 bg-muted",
          )}
        />
      )}
      {(label || material) && (
        <span className="truncate">
          {label}
          {label && material ? " " : ""}
          {material ? <span className="font-mono text-muted-foreground">{material}</span> : null}
        </span>
      )}
      {remainPct != null ? (
        <span data-slot="filament-chip-remain-tick" className="font-mono text-[10px] tabular-nums text-muted-foreground">
          {remainPct}%
        </span>
      ) : null}
      {lowStock ? (
        <Badge data-slot="filament-chip-low-badge" variant="secondary" className="h-4 px-1 text-[10px]">
          Low
        </Badge>
      ) : null}
      <span className="sr-only">{description}</span>
    </span>
  )
}

export { FilamentChip, filamentChipVariants }
