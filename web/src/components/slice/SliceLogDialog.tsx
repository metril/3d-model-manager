import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useTranslation } from "react-i18next";

/** The slicer CLI's output tail from a failed slice (`log_tail` / `last_log`). */
export function SliceLogDialog({
  open,
  onOpenChange,
  title,
  error,
  log,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: string;
  error?: string | null;
  log?: string | null;
}) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{title ?? t("sliceLog.title")}</DialogTitle>
          <DialogDescription>{error ?? t("sliceLog.lastRun")}</DialogDescription>
        </DialogHeader>
        <pre
          aria-label={t("sliceLog.output")}
          className="max-h-96 overflow-auto rounded-md bg-muted p-3 font-mono text-xs whitespace-pre-wrap"
        >
          {log && log.trim() !== "" ? log : t("sliceLog.empty")}
        </pre>
        <DialogFooter>
          {log ? (
            <Button type="button" variant="outline" onClick={() => void navigator.clipboard?.writeText(log)}>
              {t("sliceLog.copy")}
            </Button>
          ) : null}
          <Button type="button" onClick={() => onOpenChange(false)}>
            {t("sliceLog.close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
