import { PauseIcon, PlayIcon, SquareIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { usePrinterCommand } from "@/api/printers";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";

/** Pause/Resume + Stop (destructive) for a running/paused print, each behind a confirm. */
export function JobActions({ printerId, paused }: { printerId: number; paused: boolean }) {
  const { t } = useTranslation();
  const command = usePrinterCommand(printerId);
  return (
    <div className="flex items-center gap-2" data-slot="job-actions">
      <ConfirmDialog
        trigger={
          <Button type="button" size="sm" variant="outline" disabled={command.isPending}>
            {paused ? <PlayIcon /> : <PauseIcon />}
            {paused ? t("card.controls.resume") : t("card.controls.pause")}
          </Button>
        }
        title={paused ? t("card.controls.resumeTitle") : t("card.controls.pauseTitle")}
        confirmLabel={paused ? t("card.controls.resume") : t("card.controls.pause")}
        onConfirm={() => command.mutate(paused ? "resume" : "pause")}
      />
      <ConfirmDialog
        trigger={
          <Button type="button" size="sm" variant="destructive" disabled={command.isPending}>
            <SquareIcon />
            {t("card.controls.stop")}
          </Button>
        }
        title={t("card.controls.stopTitle")}
        description={t("card.controls.stopDesc")}
        confirmLabel={t("card.controls.stop")}
        destructive
        onConfirm={() => command.mutate("stop")}
      />
    </div>
  );
}
