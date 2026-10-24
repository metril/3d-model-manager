import { useState, type ReactNode } from "react";

import { useAuth } from "@/api/auth";
import { usePrinters, usePrinterStatus } from "@/api/printers";
import type { PrinterStatusOut } from "@/api/types";
import { DiagnosticModal } from "./DiagnosticModal";
import { FirmwareModal } from "./FirmwareModal";
import { FileManagerModal } from "./FileManagerModal";
import { HmsModal } from "./HmsModal";
import { PrinterInfoModal } from "./PrinterInfoModal";
import { PrintOptionsModal } from "./PrintOptionsModal";
import { capsOf } from "../card/caps";

export type PrinterModalKind = "info" | "hms" | "firmware" | "diagnostic" | "printOptions" | "files";

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
  const { canPrinter } = useAuth();
  const canControl = canPrinter(printerId, "control");
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
      {kind === "printOptions" && printer ? (
        <PrintOptionsModal printer={printer} status={status.data} canControl={canControl} open onOpenChange={close} />
      ) : null}
      {kind === "files" && printer ? (
        <FileManagerModal printerId={printerId} canControl={canControl} sdPrint={!!capsOf(printer).sd_print} open onOpenChange={close} />
      ) : null}
      {kind === "info" && printer ? (
        <PrinterInfoModal printer={printer} status={status.data} open onOpenChange={close} />
      ) : null}
    </>
  );
  return { open: setKind, modals };
}
