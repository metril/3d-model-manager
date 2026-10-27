import type { ComponentProps, ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/**
 * Printer-card tool button: icon-only (tooltip carries the label) or icon + text
 * when `showLabel`. `aria-label` always equals `label` so the accessible name is
 * stable. `untested` appends the "Untested on this model" hint to the tooltip.
 * Needs a `TooltipProvider` ancestor (the controls/toolbar roots provide one).
 */
export function ToolButton({
  label,
  icon,
  showLabel = false,
  untested = false,
  reason,
  variant = "ghost",
  ...props
}: Omit<ComponentProps<typeof Button>, "children" | "size"> & {
  label: string;
  icon: ReactNode;
  showLabel?: boolean;
  untested?: boolean;
  /** Shown in the tooltip while `disabled` (why the tool is unavailable). */
  reason?: string;
}) {
  const { t } = useTranslation();
  const button = (
    <Button type="button" variant={variant} size={showLabel ? "sm" : "icon-sm"} aria-label={label} {...props}>
      {icon}
      {showLabel ? label : null}
    </Button>
  );
  const whyDisabled = props.disabled && reason;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {whyDisabled ? (
          <span tabIndex={0} className="inline-flex">
            {button}
          </span>
        ) : (
          button
        )}
      </TooltipTrigger>
      <TooltipContent>
        <span className="block">{label}</span>
        {whyDisabled ? <span className="block opacity-70">{reason}</span> : null}
        {untested ? <span className="block opacity-70">{t("card.untested")}</span> : null}
      </TooltipContent>
    </Tooltip>
  );
}
