/**
 * Q12: the printer card's shared wiring (badges, footer items, menu modals),
 * used by BOTH the dashboard grid and `/printers/$id`. `usePrinterCardExtras`
 * is a hook (it owns `usePrinterModals`), so `ManagedPrinterCard` (separate
 * file) is the per-card wrapper that calls it and renders the modals.
 */
import { useNavigate } from "@tanstack/react-router";
import type { ReactNode } from "react";

import type { PrinterOut, PrinterStatusOut } from "@/api/types";
import { MaintenanceBadge } from "@/components/maintenance/MaintenanceBadge";
import { SharedPlugRow } from "@/components/print/PlugRow";
import { PrinterToolbar } from "@/components/print/card/PrinterToolbar";
import { usePrinterModals } from "@/components/print/modals";
import type { CardSlot } from "@/components/print/PrinterCard";
import { SharedDetectionBadge } from "@/components/printer/DetectionBadge";

export interface CardPerms {
  /** `canPrinter(id, "print")`: camera, HMS, detection, history. */
  canPrint: boolean;
  /** `can("settings:write")`: the Diagnose menu entry. */
  canAdmin: boolean;
}

export interface CardExtras {
  badgeExtra: CardSlot;
  footerExtra: CardSlot;
  onInfo: () => void;
  onDiagnose?: () => void;
  onHms?: () => void;
  onDry: (unitId: number) => void;
  modals: ReactNode;
}

export function usePrinterCardExtras(printer: PrinterOut, status: PrinterStatusOut, perms: CardPerms): CardExtras {
  const navigate = useNavigate();
  const modals = usePrinterModals(printer.id, status);
  const goDetail = () => void navigate({ to: "/printers/$id", params: { id: String(printer.id) } });
  return {
    onInfo: () => modals.open("info"),
    onDry: (unitId) => modals.open("drying", unitId),
    onDiagnose: perms.canAdmin ? () => modals.open("diagnostic") : undefined,
    onHms: perms.canPrint ? () => modals.open("hms") : undefined,
    badgeExtra: (
      <>
        <MaintenanceBadge printerId={printer.id} />
        {perms.canPrint ? <SharedDetectionBadge printerId={printer.id} /> : null}
      </>
    ),
    footerExtra: (ctx) => (
      <PrinterToolbar
        printer={printer}
        status={status}
        canControl={ctx.canControl}
        canPrint={perms.canPrint}
        size={ctx.size}
        onOpen={(kind) => modals.open(kind)}
        onCamera={goDetail}
        extra={<SharedPlugRow printerId={printer.id} canControl={ctx.canControl} />}
      />
    ),
    modals: modals.modals,
  };
}
