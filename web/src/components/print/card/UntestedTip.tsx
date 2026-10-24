import type { ReactElement } from "react";
import { useTranslation } from "react-i18next";

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

/** Wraps a control with the "Untested on this model" tooltip when `show` (capabilities.verified === false). */
export function UntestedTip({ show, children }: { show: boolean; children: ReactElement }) {
  const { t } = useTranslation();
  if (!show) return children;
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>{children}</TooltipTrigger>
        <TooltipContent>{t("card.untested")}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
