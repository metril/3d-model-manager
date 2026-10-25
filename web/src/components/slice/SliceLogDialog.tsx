import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

/** The slicer CLI's output tail from a failed slice (`log_tail` / `last_log`). */
export function SliceLogDialog({
  open,
  onOpenChange,
  title = "Slicer log",
  error,
  log,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: string;
  error?: string | null;
  log?: string | null;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{error ?? "Output of the slicer's last run."}</DialogDescription>
        </DialogHeader>
        <pre
          aria-label="Log output"
          className="max-h-96 overflow-auto rounded-md bg-muted p-3 font-mono text-xs whitespace-pre-wrap"
        >
          {log && log.trim() !== "" ? log : "No log was captured."}
        </pre>
        <DialogFooter>
          {log ? (
            <Button type="button" variant="outline" onClick={() => void navigator.clipboard?.writeText(log)}>
              Copy
            </Button>
          ) : null}
          <Button type="button" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
