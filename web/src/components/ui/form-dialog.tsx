import { useState, type ComponentProps } from "react";
import { useTranslation } from "react-i18next";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Dialog, DialogContent } from "@/components/ui/dialog";

type FormDialogProps = Omit<ComponentProps<typeof DialogContent>, "dismissable"> & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Unsaved edits: closing asks "Discard changes?" first. */
  dirty?: boolean;
  /** Save/upload/sync running: closing is ignored. */
  busy?: boolean;
};

/**
 * Dialog for edit forms: outside click / Escape never dismiss it, and every
 * close path (X, Cancel, Escape) is blocked while `busy` and confirmed while
 * `dirty`. A programmatic `onOpenChange(false)` by the caller (e.g. after a
 * successful save) bypasses the guard.
 */
export function FormDialog({ open, onOpenChange, dirty, busy, children, ...contentProps }: FormDialogProps) {
  const { t } = useTranslation();
  const [confirming, setConfirming] = useState(false);

  function requestChange(next: boolean) {
    if (next) return onOpenChange(true);
    if (busy) return;
    if (dirty) return setConfirming(true);
    onOpenChange(false);
  }

  return (
    <>
      <Dialog open={open} onOpenChange={requestChange}>
        <DialogContent dismissable={false} {...contentProps}>
          {children}
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={t("formDialog.discardTitle")}
        description={t("formDialog.discardDesc")}
        confirmLabel={t("formDialog.discard")}
        cancelLabel={t("formDialog.keepEditing")}
        destructive
        onConfirm={() => onOpenChange(false)}
      />
    </>
  );
}
