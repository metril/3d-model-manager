import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { isEditableTarget } from "@/hooks/useHotkeys";
import { setLayoutLocked, useLayoutLocked } from "@/lib/layoutLock";

/** Sticky strip shown while the layout is unlocked. Esc or Done re-locks. */
export function LayoutEditBanner({ onReset, suspendEsc }: { onReset?: () => void; suspendEsc?: boolean }) {
  const { t } = useTranslation();
  const locked = useLayoutLocked();
  // Never leave the (module-level) unlock flag set after leaving the page.
  useEffect(() => () => setLayoutLocked(true), []);
  useEffect(() => {
    if (locked || suspendEsc) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented && !isEditableTarget(e.target)) setLayoutLocked(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [locked, suspendEsc]);
  if (locked) return null;
  return (
    <div
      data-testid="layout-edit-banner"
      className="sticky top-0 z-20 mb-2 flex flex-wrap items-center gap-2 rounded-md border border-primary/30 bg-primary/10 px-3 py-2 text-sm"
    >
      <span className="flex-1 text-foreground">{t("layout.banner")}</span>
      {onReset ? (
        <Button type="button" size="sm" variant="ghost" onClick={onReset}>
          {t("layout.reset")}
        </Button>
      ) : null}
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button type="button" size="sm" onClick={() => setLayoutLocked(true)}>
              {t("layout.done")}
            </Button>
          </TooltipTrigger>
          <TooltipContent>{t("layout.doneHint")}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  );
}
