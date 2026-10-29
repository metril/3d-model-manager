import { type ComponentProps } from "react";

import { Sheet, SheetContent } from "@/components/ui/sheet";
import { useDiscardGuard } from "@/components/ui/use-discard-guard";

type FormSheetProps = Omit<ComponentProps<typeof SheetContent>, "dismissable"> & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Unsaved edits: closing asks "Discard changes?" first. */
  dirty?: boolean;
  /** Save running: closing is ignored. */
  busy?: boolean;
};

/** Side-sheet counterpart of FormDialog: same dismissal and discard rules. */
export function FormSheet({ open, onOpenChange, dirty, busy, children, ...contentProps }: FormSheetProps) {
  const { requestChange, confirm } = useDiscardGuard({ onOpenChange, dirty, busy });
  return (
    <>
      <Sheet open={open} onOpenChange={requestChange}>
        <SheetContent dismissable={false} {...contentProps}>
          {children}
        </SheetContent>
      </Sheet>
      {confirm}
    </>
  );
}
