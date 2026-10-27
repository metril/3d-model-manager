/**
 * Printer tools: Move and Camera inline (ghost icons with tooltips) plus one
 * grouped overflow menu, so every capability stays reachable:
 *   Job      Files, Print options, Skip objects
 *   Maintain Calibrate (confirm, disabled while busy), K-profiles, Firmware
 *   Insight  Heater history, AMS history, Diagnose, Info
 * Capability/permission gating is unchanged.
 */
import {
  CpuIcon,
  FolderIcon,
  GaugeIcon,
  HistoryIcon,
  InfoIcon,
  ListXIcon,
  MoreHorizontalIcon,
  SlidersHorizontalIcon,
  StethoscopeIcon,
  ThermometerIcon,
  WrenchIcon,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useControl, usePrinterObjects } from "@/api/printerControl";
import type { PrinterOut, PrinterStatusOut } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { CameraSplitButton } from "@/components/print/CameraSplitButton";
import { AmsHistoryModal } from "@/components/print/modals/AmsHistoryModal";
import { HeaterHistoryModal } from "@/components/print/modals/HeaterHistoryModal";
import type { PrinterModalKind } from "@/components/print/modals/usePrinterModals";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { TooltipProvider } from "@/components/ui/tooltip";
import { toneForGcode } from "@/lib/printerState";

import { capsOf, isBusy } from "./caps";
import { MovementPopover } from "./MovementPopover";
import { SkipObjectsDialog } from "./SkipObjectsDialog";
import { ToolButton } from "./ToolButton";
import type { CardSize } from "./cardSize";

type GroupId = "job" | "maintain" | "insight";

interface Tool {
  id: string;
  group: GroupId;
  label: string;
  icon: ReactNode;
  disabled?: boolean;
  title?: string;
  run: () => void;
}

const GROUPS: { id: GroupId; key: string }[] = [
  { id: "job", key: "card.tools.groupJob" },
  { id: "maintain", key: "card.tools.groupMaintain" },
  { id: "insight", key: "card.tools.groupInsight" },
];

export function PrinterToolbar({
  printer,
  status,
  canControl,
  canPrint,
  canAdmin = false,
  size = "M",
  onOpen,
  onCamera,
  extra,
}: {
  printer: PrinterOut;
  status: PrinterStatusOut;
  canControl: boolean;
  canPrint: boolean;
  /** `settings:write`: gates Diagnose. */
  canAdmin?: boolean;
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
  const [skipOpen, setSkipOpen] = useState(false);
  const showLabel = size === "XL";
  const busy = isBusy(status.gcode_state);
  const live = canControl && status.online;
  const untested = !caps.verified;
  const amsUnits = status.ams_units?.map((u) => u.id) ?? [];
  const { tone } = toneForGcode(status.online, status.gcode_state);
  const objects = usePrinterObjects(printer.id, (tone === "printing" || tone === "paused") && canControl);
  const skippable = objects.data?.objects ?? [];
  const busyReason = t("card.controls.busyReason");

  const candidates: (Tool | null)[] = [
    canPrint && caps.sd_browser
      ? { id: "files", group: "job", label: t("printerOps.files.button"), icon: <FolderIcon />, run: () => onOpen("files") }
      : null,
    canControl
      ? {
          id: "printOptions",
          group: "job",
          label: t("printerOps.printOptions.menu"),
          icon: <SlidersHorizontalIcon />,
          run: () => onOpen("printOptions"),
        }
      : null,
    canControl && skippable.length > 1
      ? {
          id: "skip",
          group: "job",
          label: `${t("card.status.skip")} (${skippable.length})`,
          icon: <ListXIcon />,
          run: () => setSkipOpen(true),
        }
      : null,
    live
      ? {
          id: "calibrate",
          group: "maintain",
          label: t("card.controls.calibration"),
          icon: <WrenchIcon />,
          disabled: busy,
          title: busy ? busyReason : undefined,
          run: () => setCalibrateOpen(true),
        }
      : null,
    canControl && caps.kprofiles
      ? { id: "kprofiles", group: "maintain", label: t("kprofile.menu"), icon: <GaugeIcon />, run: () => onOpen("kprofiles") }
      : null,
    { id: "firmware", group: "maintain", label: t("card.footer.firmware"), icon: <CpuIcon />, run: () => onOpen("firmware") },
    canPrint
      ? { id: "heater", group: "insight", label: t("history.heaterBtn"), icon: <ThermometerIcon />, run: () => setHistory("heater") }
      : null,
    canPrint && amsUnits.length > 0
      ? { id: "ams", group: "insight", label: t("history.amsBtn"), icon: <HistoryIcon />, run: () => setHistory("ams") }
      : null,
    canAdmin
      ? { id: "diagnose", group: "insight", label: t("card.header.diagnose"), icon: <StethoscopeIcon />, run: () => onOpen("diagnostic") }
      : null,
    { id: "info", group: "insight", label: t("card.header.info"), icon: <InfoIcon />, run: () => onOpen("info") },
  ];
  const tools = candidates.filter((x): x is Tool => x !== null);
  const groups = GROUPS.map((g) => ({ ...g, tools: tools.filter((x) => x.group === g.id) })).filter((g) => g.tools.length > 0);

  return (
    <TooltipProvider delayDuration={200}>
      {extra}
      <div className="flex items-center gap-0.5" role="toolbar" aria-label={t("card.tools.label")} data-slot="printer-toolbar">
        {live ? <MovementPopover printerId={printer.id} busy={busy} untested={untested} showLabel={showLabel} /> : null}
        {canPrint ? <CameraSplitButton printerId={printer.id} onInline={onCamera} showLabel={showLabel} /> : null}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <ToolButton label={t("card.tools.more")} icon={<MoreHorizontalIcon />} />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="min-w-48">
            {groups.map((g, i) => (
              <DropdownMenuGroup key={g.id}>
                {i > 0 ? <DropdownMenuSeparator /> : null}
                <DropdownMenuLabel className="text-xs text-muted-foreground">
                  {t(g.key)}
                </DropdownMenuLabel>
                {g.tools.map((x) => (
                  <DropdownMenuItem key={x.id} disabled={x.disabled} title={x.title} onSelect={x.run}>
                    {x.icon}
                    {x.label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuGroup>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
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
      {skippable.length > 1 ? (
        <SkipObjectsDialog printerId={printer.id} objects={skippable} open={skipOpen} onOpenChange={setSkipOpen} />
      ) : null}
      <HeaterHistoryModal printerId={printer.id} open={history === "heater"} onOpenChange={(o) => !o && setHistory(null)} />
      <AmsHistoryModal printerId={printer.id} units={amsUnits} open={history === "ams"} onOpenChange={(o) => !o && setHistory(null)} />
    </TooltipProvider>
  );
}
