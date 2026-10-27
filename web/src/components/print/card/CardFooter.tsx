/**
 * Footer: tools on the left (`footer-extra` slot: plug row + `PrinterToolbar`),
 * the state-aware primary action pinned right -- Pause/Resume + Stop while a
 * print is running/paused (when the viewer can control), otherwise Print.
 */
import { PlayIcon } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { PrintDialog } from "@/components/print/PrintDialog";
import { Button } from "@/components/ui/button";

import { JobActions } from "./JobActions";

export function CardFooter({
  printerId,
  canPrint,
  canControl = false,
  online,
  gcodeState,
  extra,
}: {
  printerId?: number;
  canPrint: boolean;
  canControl?: boolean;
  online: boolean;
  gcodeState?: string | null;
  /** `footer-extra` slot: toolbar + plug row. */
  extra?: ReactNode;
}) {
  const { t } = useTranslation();
  const [printOpen, setPrintOpen] = useState(false);
  const running = gcodeState === "RUNNING" || gcodeState === "PREPARE";
  const paused = gcodeState === "PAUSE";
  const jobActions = (running || paused) && canControl && online && printerId != null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-t pt-3" data-slot="card-footer">
      {extra ? <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-2">{extra}</div> : <span className="flex-1" />}
      <div className="ml-auto flex items-center gap-2" data-slot="primary-action">
        {jobActions ? (
          <JobActions printerId={printerId} paused={paused} />
        ) : canPrint ? (
          <Button type="button" size="sm" disabled={!online} onClick={() => setPrintOpen(true)}>
            <PlayIcon />
            {t("card.footer.print")}
          </Button>
        ) : null}
      </div>
      {printOpen ? <PrintDialog open onOpenChange={setPrintOpen} source={{ kind: "pick" }} printerId={printerId} /> : null}
    </div>
  );
}
