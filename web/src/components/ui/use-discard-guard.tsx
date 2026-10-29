import { useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { ConfirmDialog } from "@/components/ConfirmDialog";

/**
 * Close guard shared by FormDialog and FormSheet: closing is ignored while
 * `busy` and confirmed ("Discard changes?") while `dirty`. A programmatic
 * `onOpenChange(false)` by the caller (e.g. after a successful save) bypasses it.
 */
export function useDiscardGuard({
  onOpenChange,
  dirty,
  busy,
}: {
  onOpenChange: (open: boolean) => void;
  dirty?: boolean;
  busy?: boolean;
}): { requestChange: (next: boolean) => void; confirm: ReactNode } {
  const { t } = useTranslation();
  const [confirming, setConfirming] = useState(false);
  const returnFocus = useRef<HTMLElement | null>(null);

  function requestChange(next: boolean) {
    if (next) return onOpenChange(true);
    if (busy) return;
    if (dirty) {
      returnFocus.current = document.activeElement as HTMLElement | null;
      return setConfirming(true);
    }
    onOpenChange(false);
  }

  const confirm = (
    <ConfirmDialog
      open={confirming}
      onOpenChange={setConfirming}
      title={t("formDialog.discardTitle")}
      description={t("formDialog.discardDesc")}
      confirmLabel={t("formDialog.discard")}
      onCancel={() => returnFocus.current?.focus()}
      cancelLabel={t("formDialog.keepEditing")}
      destructive
      onConfirm={() => onOpenChange(false)}
    />
  );
  return { requestChange, confirm };
}
