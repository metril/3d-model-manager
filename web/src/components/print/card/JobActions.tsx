import { PauseIcon, PlayIcon, SquareIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { usePrinterCommand } from "@/api/printers";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";

/**
 * Pause (secondary, wide) or Resume (filled) plus Stop (outline, destructive
 * text/border), each behind a confirm. Renders as the two cells of the
 * primary-action grid.
 */
export function JobActions({ printerId, paused }: { printerId: number; paused: boolean }) {
  const { t } = useTranslation();
  const command = usePrinterCommand(printerId);
  return (
    <>
      <ConfirmDialog
        trigger={
          <Button type="button" variant={paused ? "default" : "secondary"} className="w-full" disabled={command.isPending}>
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
          <Button
            type="button"
            variant="outline"
            className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
            disabled={command.isPending}
          >
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
    </>
  );
}
