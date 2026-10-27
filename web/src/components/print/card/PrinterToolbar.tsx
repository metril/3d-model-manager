/**
 * Printer tools, one consistent toolbar: icon buttons with tooltips for the
 * frequent ones and a "More" overflow menu for the rest. Move and Camera are
 * always inline; Files and Print options join them at L/XL (labelled at XL);
 * everything else (K-profiles, Calibrate, Firmware, temperature/AMS history,
 * plus Files/Print options at M) lives in the menu, so every capability stays
 * reachable at every size. Capability/permission gating is unchanged.
 */
import {
  CpuIcon,
  FolderIcon,
  GaugeIcon,
  HistoryIcon,
  MoreHorizontalIcon,
  SlidersHorizontalIcon,
  ThermometerIcon,
  WrenchIcon,
} from "lucide-react";
import { Fragment, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useControl } from "@/api/printerControl";
import type { PrinterOut, PrinterStatusOut } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { CameraSplitButton } from "@/components/print/CameraSplitButton";
import { AmsHistoryModal } from "@/components/print/modals/AmsHistoryModal";
import { HeaterHistoryModal } from "@/components/print/modals/HeaterHistoryModal";
import type { PrinterModalKind } from "@/components/print/modals/usePrinterModals";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { TooltipProvider } from "@/components/ui/tooltip";

import { capsOf, isBusy } from "./caps";
import { MovementPopover } from "./MovementPopover";
import { ToolButton } from "./ToolButton";
import type { CardSize } from "./cardSize";

interface Tool {
  id: string;
  group: number;
  label: string;
  icon: ReactNode;
  disabled?: boolean;
  untested?: boolean;
  /** Rendered as an inline icon button at L/XL; otherwise menu-only. */
  inlineAtLarge?: boolean;
  run: () => void;
}

export function PrinterToolbar({
  printer,
  status,
  canControl,
  canPrint,
  size = "M",
  onOpen,
  onCamera,
  extra,
}: {
  printer: PrinterOut;
  status: PrinterStatusOut;
  canControl: boolean;
  canPrint: boolean;
  size?: CardSize;
  onOpen: (kind: PrinterModalKind) => void;
  onCamera?: () => void;
  /** Rendered before the tools (smart-plug row). */
  extra?: ReactNode;
}) {
  const { t } = useTranslation();
  const caps = capsOf(printer);
  const control = useControl(printer.id);
  const [history, setHistory] = useState<"heater" | "ams" | null>(null);
  const [calibrateOpen, setCalibrateOpen] = useState(false);
  const large = size === "L" || size === "XL";
  const showLabel = size === "XL";
  const busy = isBusy(status.gcode_state);
  const live = canControl && status.online;
  const untested = !caps.verified;
  const amsUnits = status.ams_units?.map((u) => u.id) ?? [];

  const candidates: (Tool | null)[] = [
    canPrint && caps.sd_browser
      ? { id: "files", group: 0, label: t("printerOps.files.button"), icon: <FolderIcon />, inlineAtLarge: true, run: () => onOpen("files") }
      : null,
    canControl
      ? {
          id: "printOptions",
          group: 0,
          label: t("printerOps.printOptions.menu"),
          icon: <SlidersHorizontalIcon />,
          untested,
          inlineAtLarge: true,
          run: () => onOpen("printOptions"),
        }
      : null,
    canControl && caps.kprofiles
      ? { id: "kprofiles", group: 0, label: t("kprofile.menu"), icon: <GaugeIcon />, run: () => onOpen("kprofiles") }
      : null,
    live
      ? {
          id: "calibrate",
          group: 1,
          label: t("card.controls.calibration"),
          icon: <WrenchIcon />,
          disabled: busy,
          run: () => setCalibrateOpen(true),
        }
      : null,
    { id: "firmware", group: 2, label: t("card.footer.firmware"), icon: <CpuIcon />, run: () => onOpen("firmware") },
    canPrint
      ? { id: "heater", group: 2, label: t("history.heaterBtn"), icon: <ThermometerIcon />, run: () => setHistory("heater") }
      : null,
    canPrint && amsUnits.length > 0
      ? { id: "ams", group: 2, label: t("history.amsBtn"), icon: <HistoryIcon />, run: () => setHistory("ams") }
      : null,
  ];
  const tools = candidates.filter((x): x is Tool => x !== null);

  const inline = tools.filter((x) => large && x.inlineAtLarge);
  const menu = tools.filter((x) => !(large && x.inlineAtLarge));

  return (
    <TooltipProvider delayDuration={200}>
      {extra}
      <div className="flex items-center gap-0.5" role="toolbar" aria-label={t("card.tools.label")} data-slot="printer-toolbar">
        {live ? <MovementPopover printerId={printer.id} busy={busy} untested={untested} showLabel={showLabel} /> : null}
        {canPrint ? <CameraSplitButton printerId={printer.id} onInline={onCamera} showLabel={showLabel} /> : null}
        {inline.map((x) => (
          <ToolButton
            key={x.id}
            label={x.label}
            icon={x.icon}
            showLabel={showLabel}
            untested={x.untested}
            disabled={x.disabled}
            onClick={x.run}
          />
        ))}
        {menu.length > 0 ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <ToolButton label={t("card.tools.more")} icon={<MoreHorizontalIcon />} />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start">
              {menu.map((x, i) => (
                <Fragment key={x.id}>
                  {i > 0 && menu[i - 1].group !== x.group ? <DropdownMenuSeparator /> : null}
                  <DropdownMenuItem disabled={x.disabled} onSelect={x.run}>
                    {x.icon}
                    {x.label}
                  </DropdownMenuItem>
                </Fragment>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </div>
      {live ? (
        <ConfirmDialog
          open={calibrateOpen}
          onOpenChange={setCalibrateOpen}
          title={t("card.controls.calibrateTitle")}
          description={t("card.controls.calibrateDesc")}
          confirmLabel={t("card.controls.calibrate")}
          onConfirm={() => void control.send("calibrate", {})}
        />
      ) : null}
      <HeaterHistoryModal printerId={printer.id} open={history === "heater"} onOpenChange={(o) => !o && setHistory(null)} />
      <AmsHistoryModal printerId={printer.id} units={amsUnits} open={history === "ams"} onOpenChange={(o) => !o && setHistory(null)} />
    </TooltipProvider>
  );
}
