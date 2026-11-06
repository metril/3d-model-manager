import { useRef, useState, type ReactNode } from "react";
import { Loader2Icon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

interface ConfirmDialogProps {
  /** Omit when the dialog is opened from elsewhere (e.g. a dropdown menu
   * item) via the controlled `open`/`onOpenChange` pair below -- a
   * `DialogTrigger` nested inside a menu item would unmount along with the
   * menu when it closes, taking the dialog with it. */
  trigger?: ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Called when the dialog is dismissed without confirming. */
  onCancel?: () => void;
  destructive?: boolean;
  confirmDisabled?: boolean;
  /** Opt into async mode: Confirm no longer closes the dialog; while true it
   * shows a spinner and can't be dismissed. The caller closes it when done. */
  pending?: boolean;
  onConfirm: () => void;
}

/** Reusable confirm-then-act dialog (Task 8: archive model, delete file). */
export function ConfirmDialog({
  trigger,
  open: openProp,
  onOpenChange,
  title,
  description,
  confirmLabel,
  cancelLabel,
  onCancel,
  destructive,
  confirmDisabled,
  pending: pendingProp,
  onConfirm,
}: ConfirmDialogProps) {
  const [internalOpen, setInternalOpen] = useState(false);
  const isControlled = openProp !== undefined;
  const open = isControlled ? openProp : internalOpen;
  const rawSetOpen = isControlled ? (onOpenChange ?? (() => {})) : setInternalOpen;
  const { t } = useTranslation();
  const pending = pendingProp === true;
  // Set when Confirm closes the dialog so the close-focus hook doesn't also
  // report a cancel.
  const confirmed = useRef(false);
  const setOpen = (next: boolean) => {
    if (pending && !next) return;
    if (next) confirmed.current = false;
    rawSetOpen(next);
  };
  const onCloseAutoFocus = onCancel
    ? (e: Event) => {
        e.preventDefault();
        if (confirmed.current) {
          confirmed.current = false;
          return;
        }
        onCancel();
      }
    : undefined;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      {trigger ? <DialogTrigger asChild>{trigger}</DialogTrigger> : null}
      <DialogContent onCloseAutoFocus={onCloseAutoFocus}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description ? <DialogDescription>{description}</DialogDescription> : null}
        </DialogHeader>
        <DialogFooter showCloseButton closeLabel={cancelLabel}>
          <Button
            type="button"
            disabled={confirmDisabled || pending}
            variant={destructive ? "destructive" : "default"}
            onClick={() => {
              // Passing `pending` (even false) opts into async mode: the dialog
              // stays open and the caller closes it when the action ends.
              if (pendingProp === undefined) {
                confirmed.current = true;
                rawSetOpen(false);
              }
              onConfirm();
            }}
          >
            {pending ? <Loader2Icon className="size-4 animate-spin" aria-hidden /> : null}
            {confirmLabel ?? t("common.confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
