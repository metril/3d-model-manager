import { useEffect } from "react";
import { CheckIcon, PencilIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { setLayoutLocked, useLayoutLocked } from "@/lib/layoutLock";

/** Toggles the global layout lock. Esc re-locks while unlocked. */
export function LayoutLockToggle() {
  const { t } = useTranslation();
  const locked = useLayoutLocked();
  useEffect(() => {
    if (locked) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) setLayoutLocked(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [locked]);
  return (
    <Button
      type="button"
      variant={locked ? "ghost" : "secondary"}
      size="sm"
      aria-pressed={!locked}
      title={locked ? t("layoutLock.unlockHint") : t("layoutLock.lockHint")}
      onClick={() => setLayoutLocked(!locked)}
    >
      {locked ? <PencilIcon /> : <CheckIcon />}
      {locked ? t("layoutLock.edit") : t("layoutLock.done")}
    </Button>
  );
}
