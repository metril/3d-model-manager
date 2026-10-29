import { type ComponentProps } from "react";

import { Dialog, DialogContent } from "@/components/ui/dialog";
import { useDiscardGuard } from "@/components/ui/use-discard-guard";

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
  const { requestChange, confirm } = useDiscardGuard({ onOpenChange, dirty, busy });
  return (
    <>
      <Dialog open={open} onOpenChange={requestChange}>
        <DialogContent dismissable={false} {...contentProps}>
          {children}
        </DialogContent>
      </Dialog>
      {confirm}
    </>
  );
}
