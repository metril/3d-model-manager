/**
 * Primary-action row (`grid-cols-[1fr_auto]`), state-aware:
 *   idle     full-width Print
 *   printing Pause (wide) + Stop        paused  Resume (filled) + Stop
 *   offline  "Offline" + Power on (when a smart plug is linked)
 * Viewers without control keep Print while a job runs.
 */
import { PlayIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { PrintDialog } from "@/components/print/PrintDialog";
import { SharedPlugPowerOn } from "@/components/print/PlugRow";
import { Button } from "@/components/ui/button";

import { JobActions } from "./JobActions";

export function CardFooter({
  printerId,
  canPrint,
  canControl = false,
  online,
  gcodeState,
}: {
  printerId?: number;
  canPrint: boolean;
  canControl?: boolean;
  online: boolean;
  gcodeState?: string | null;
}) {
  const { t } = useTranslation();
  const [printOpen, setPrintOpen] = useState(false);
  const running = gcodeState === "RUNNING" || gcodeState === "PREPARE";
  const paused = gcodeState === "PAUSE";
  const jobActions = (running || paused) && canControl && online && printerId != null;
  if (!online) {
    return (
      <div className="flex items-center justify-between gap-2" data-slot="card-footer">
        <span className="pc-t12 text-muted-foreground">{t("card.footer.unreachable")}</span>
        {printerId != null ? <SharedPlugPowerOn printerId={printerId} canControl={canControl} label={t("card.footer.powerOn")} /> : null}
      </div>
    );
  }
  if (!jobActions && !canPrint) return null;
  return (
    <div className="grid grid-cols-[1fr_auto] gap-2" data-slot="card-footer">
      {jobActions ? (
        <JobActions printerId={printerId} paused={paused} />
      ) : (
        <Button type="button" className="col-span-2 w-full" onClick={() => setPrintOpen(true)}>
          <PlayIcon />
          {t("card.footer.print")}
        </Button>
      )}
      {printOpen ? <PrintDialog open onOpenChange={setPrintOpen} source={{ kind: "pick" }} printerId={printerId} /> : null}
    </div>
  );
}
