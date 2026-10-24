/**
 * Q12: the printer card's shared wiring (badges, footer items, menu modals),
 * used by BOTH the dashboard grid and `/printers/$id`. `usePrinterCardExtras`
 * is a hook (it owns `usePrinterModals`), so `ManagedPrinterCard` (separate
 * file) is the per-card wrapper that calls it and renders the modals.
 */
import { useNavigate } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { PrinterOut, PrinterStatusOut } from "@/api/types";
import { MaintenanceBadge } from "@/components/maintenance/MaintenanceBadge";
import { CameraSplitButton } from "@/components/print/CameraSplitButton";
import { HistoryButtons } from "@/components/print/HistoryButtons";
import { SharedPlugRow } from "@/components/print/PlugRow";
import { usePrinterModals } from "@/components/print/modals";
import type { CardSlot } from "@/components/print/PrinterCard";
import { SharedDetectionBadge } from "@/components/printer/DetectionBadge";
import { capsOf } from "@/components/print/card/caps";
import { UntestedTip } from "@/components/print/card/UntestedTip";
import { Button } from "@/components/ui/button";

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
  modals: ReactNode;
}

export function usePrinterCardExtras(printer: PrinterOut, status: PrinterStatusOut, perms: CardPerms): CardExtras {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const modals = usePrinterModals(printer.id, status);
  const amsUnits = status.ams_units?.map((u) => u.id) ?? [];
  const goDetail = () => void navigate({ to: "/printers/$id", params: { id: String(printer.id) } });
  return {
    onInfo: () => modals.open("info"),
    onDiagnose: perms.canAdmin ? () => modals.open("diagnostic") : undefined,
    onHms: perms.canPrint ? () => modals.open("hms") : undefined,
    badgeExtra: (
      <>
        <MaintenanceBadge printerId={printer.id} />
        {perms.canPrint ? <SharedDetectionBadge printerId={printer.id} /> : null}
      </>
    ),
    footerExtra: (ctx) => (
      <>
        {perms.canPrint ? <CameraSplitButton printerId={printer.id} onInline={goDetail} /> : null}
        <SharedPlugRow printerId={printer.id} canControl={ctx.canControl} />
        {ctx.canControl ? (
          <UntestedTip show={capsOf(printer).verified === false}>
            <Button variant="outline" size="sm" onClick={() => modals.open("printOptions")}>
              {t("printerOps.printOptions.menu")}
            </Button>
          </UntestedTip>
        ) : null}
        {capsOf(printer).sd_browser ? (
          <Button variant="outline" size="sm" onClick={() => modals.open("files")}>
            {t("printerOps.files.button")}
          </Button>
        ) : null}
        <Button variant="outline" size="sm" onClick={() => modals.open("firmware")}>
          {t("card.footer.firmware")}
        </Button>
        {perms.canPrint ? <HistoryButtons printerId={printer.id} amsUnits={amsUnits} /> : null}
      </>
    ),
    modals: modals.modals,
  };
}
