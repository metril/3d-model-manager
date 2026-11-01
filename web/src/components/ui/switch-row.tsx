import * as React from "react"

import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { cn } from "@/lib/utils"

/** Padding class that lines content up with a SwitchRow's label text. */
export const SWITCH_INDENT = "pl-(--switch-indent)"

type SwitchRowProps = {
  label: React.ReactNode
  description?: React.ReactNode
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  disabled?: boolean
  /** Nested under a parent row: aligns with the parent label's text column. */
  indent?: boolean
  id?: string
  className?: string
  /** Child fields; indented to the label's text column. */
  children?: React.ReactNode
}

/**
 * Switch + clickable label (+ optional description). The switch (1.15rem) is
 * centred on the label's first line (leading-5 = 1.25rem) so multi-line text
 * wraps without drifting.
 */
function SwitchRow({
  label,
  description,
  checked,
  onCheckedChange,
  disabled,
  indent,
  id,
  className,
  children,
}: SwitchRowProps) {
  const auto = React.useId()
  const sid = id ?? auto
  return (
    <div data-slot="switch-row" className={cn(indent && SWITCH_INDENT, className)}>
      <div className="flex items-start gap-3">
        <Switch
          id={sid}
          checked={checked}
          disabled={disabled}
          onCheckedChange={onCheckedChange}
          className="mt-[calc((1.25rem-1.15rem)/2)]"
        />
        <div className="min-w-0 flex-1">
          <Label htmlFor={sid} className="leading-5">
            {label}
          </Label>
          {description ? (
            <p className="text-xs text-muted-foreground">{description}</p>
          ) : null}
        </div>
      </div>
      {children ? <div className={cn("mt-2", SWITCH_INDENT)}>{children}</div> : null}
    </div>
  )
}

export { SwitchRow }
