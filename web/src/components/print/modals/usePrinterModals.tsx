import { useState, type ReactNode } from "react";

import { usePrinters, usePrinterStatus } from "@/api/printers";
import type { PrinterStatusOut } from "@/api/types";
import { DiagnosticModal } from "./DiagnosticModal";
import { FirmwareModal } from "./FirmwareModal";
import { HmsModal } from "./HmsModal";
import { PrinterInfoModal } from "./PrinterInfoModal";

export type PrinterModalKind = "info" | "hms" | "firmware" | "diagnostic";

/** Shared by the detail page now and the Q3 card later: `open(kind)` + render `modals`. */
export function usePrinterModals(
  printerId: number,
  /** The card's overview status; without it the Info modal polls `/status` itself (only while open). */
  liveStatus?: PrinterStatusOut,
): {
  open: (kind: PrinterModalKind) => void;
  modals: ReactNode;
} {
  const [kind, setKind] = useState<PrinterModalKind | null>(null);
  const printers = usePrinters();
  const polled = usePrinterStatus(printerId, { enabled: kind === "info" && !liveStatus });
  const status = { data: liveStatus ?? polled.data };
  const printer = printers.data?.find((p) => p.id === printerId);
  const close = (o: boolean) => {
    if (!o) setKind(null);
  };

  const modals = (
    <>
      {kind === "hms" ? <HmsModal printerId={printerId} open onOpenChange={close} /> : null}
      {kind === "firmware" ? <FirmwareModal printerId={printerId} open onOpenChange={close} /> : null}
      {kind === "diagnostic" ? (
        <DiagnosticModal printerId={printerId} open onOpenChange={close} />
      ) : null}
      {kind === "info" && printer ? (
        <PrinterInfoModal printer={printer} status={status.data} open onOpenChange={close} />
      ) : null}
    </>
  );
  return { open: setKind, modals };
}
