import type { ReactElement } from "react";

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

import { UNTESTED_TIP } from "./caps";

/** Wraps a control with the "Untested on this model" tooltip when `show` (capabilities.verified === false). */
export function UntestedTip({ show, children }: { show: boolean; children: ReactElement }) {
  if (!show) return children;
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>{children}</TooltipTrigger>
        <TooltipContent>{UNTESTED_TIP}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
