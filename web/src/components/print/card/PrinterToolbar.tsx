/**
 * Printer tools: Move and Camera inline (ghost icons with tooltips) plus one
 * grouped overflow menu, so every capability stays reachable:
 *   Job      Files, Print options, Skip objects
 *   Maintain Calibrate (confirm, disabled while busy), K-profiles, Firmware
 *   Insight  Heater history, AMS history, Diagnose, Info
 *   Printer  Edit (admin), Reconnect, Force refresh, Delete (admin)
 * Capability/permission gating is unchanged.
 */
import {
  CpuIcon,
  FolderIcon,
  GaugeIcon,
  HistoryIcon,
  InfoIcon,
  ListXIcon,
  PlugZapIcon,
  RefreshCwIcon,
  SettingsIcon,
  Trash2Icon,
  SlidersHorizontalIcon,
  StethoscopeIcon,
  ThermometerIcon,
  WrenchIcon,
  type LucideIcon,
} from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { api } from "@/api/client";
import { useControl, usePrinterObjects } from "@/api/printerControl";
import { useDeletePrinter } from "@/api/printers";
import type { PrinterOut, PrinterStatusOut, ProbeOut } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { CameraSplitButton } from "@/components/print/CameraSplitButton";
import { AmsHistoryModal } from "@/components/print/modals/AmsHistoryModal";
import { HeaterHistoryModal } from "@/components/print/modals/HeaterHistoryModal";
import type { PrinterModalKind } from "@/components/print/modals/usePrinterModals";
import { OverflowMenu, type OverflowItem } from "@/components/ui/overflow-menu";
import { TooltipProvider } from "@/components/ui/tooltip";
import { toneForGcode } from "@/lib/printerState";

import { capsOf, isBusy } from "./caps";
import { MovementPopover } from "./MovementPopover";
import { SkipObjectsDialog } from "./SkipObjectsDialog";
import type { CardSize } from "./cardSize";

type GroupId = "job" | "maintain" | "insight" | "printer";

interface Tool {
  id: string;
  group: GroupId;
  label: string;
  icon: LucideIcon;
  destructive?: boolean;
  disabled?: boolean;
  title?: string;
  run: () => void;
}

const GROUPS: { id: GroupId; key: string }[] = [
  { id: "job", key: "card.tools.groupJob" },
  { id: "maintain", key: "card.tools.groupMaintain" },
  { id: "insight", key: "card.tools.groupInsight" },
  { id: "printer", key: "card.tools.groupPrinter" },
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
  const navigate = useNavigate();
  const qc = useQueryClient();
  const del = useDeletePrinter();
  const [confirmDelete, setConfirmDelete] = useState(false);
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

  async function reconnect() {
    try {
      const res = await api.post<ProbeOut>(`/printers/${printer.id}/test`);
      if (res.ok) toast.success(t("card.header.reachable"));
      else toast.error(res.detail);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("card.header.reconnectFailed"));
    }
    void qc.invalidateQueries({ queryKey: ["printers"] });
  }

  const candidates: (Tool | null)[] = [
    canPrint && caps.sd_browser
      ? { id: "files", group: "job", label: t("printerOps.files.button"), icon: FolderIcon, run: () => onOpen("files") }
      : null,
    canControl
      ? {
          id: "printOptions",
          group: "job",
          label: t("printerOps.printOptions.menu"),
          icon: SlidersHorizontalIcon,
          run: () => onOpen("printOptions"),
        }
      : null,
    canControl && skippable.length > 1
      ? {
          id: "skip",
          group: "job",
          label: `${t("card.status.skip")} (${skippable.length})`,
          icon: ListXIcon,
          run: () => setSkipOpen(true),
        }
      : null,
    live
      ? {
          id: "calibrate",
          group: "maintain",
          label: t("card.controls.calibration"),
          icon: WrenchIcon,
          disabled: busy,
          title: busy ? busyReason : undefined,
          run: () => setCalibrateOpen(true),
        }
      : null,
    canControl && caps.kprofiles
      ? { id: "kprofiles", group: "maintain", label: t("kprofile.menu"), icon: GaugeIcon, run: () => onOpen("kprofiles") }
      : null,
    { id: "firmware", group: "maintain", label: t("card.footer.firmware"), icon: CpuIcon, run: () => onOpen("firmware") },
    canPrint
      ? { id: "heater", group: "insight", label: t("history.heaterBtn"), icon: ThermometerIcon, run: () => setHistory("heater") }
      : null,
    canPrint && amsUnits.length > 0
      ? { id: "ams", group: "insight", label: t("history.amsBtn"), icon: HistoryIcon, run: () => setHistory("ams") }
      : null,
    canAdmin
      ? { id: "diagnose", group: "insight", label: t("card.header.diagnose"), icon: StethoscopeIcon, run: () => onOpen("diagnostic") }
      : null,
    ...(canAdmin
      ? [{ id: "edit", group: "printer" as const, label: t("card.header.edit"), icon: SettingsIcon, run: () => void navigate({ to: "/settings" }) }]
      : []),
    { id: "reconnect", group: "printer", label: t("card.header.reconnect"), icon: PlugZapIcon, run: () => void reconnect() },
    {
      id: "refresh",
      group: "printer",
      label: t("card.header.forceRefresh"),
      icon: RefreshCwIcon,
      run: () => void qc.invalidateQueries({ queryKey: ["printers"] }),
    },
    ...(canAdmin
      ? [{ id: "delete", group: "printer" as const, label: t("card.header.delete"), icon: Trash2Icon, destructive: true, run: () => setConfirmDelete(true) }]
      : []),
    { id: "info", group: "insight", label: t("card.header.info"), icon: InfoIcon, run: () => onOpen("info") },
  ];
  const tools = candidates.filter((x): x is Tool => x !== null);
  const groups = GROUPS.map((g) => ({
    label: t(g.key),
    items: tools
      .filter((x) => x.group === g.id)
      .map((x): OverflowItem => ({
        id: x.id,
        label: x.label,
        icon: x.icon,
        disabled: x.disabled,
        destructive: x.destructive,
        title: x.title,
        onSelect: x.run,
      })),
  }));

  return (
    <TooltipProvider delayDuration={200}>
      {extra}
      <div className="flex items-center gap-0.5" role="toolbar" aria-label={t("card.tools.label")} data-slot="printer-toolbar">
        {live ? <MovementPopover printerId={printer.id} busy={busy} untested={untested} showLabel={showLabel} /> : null}
        {canPrint ? <CameraSplitButton printerId={printer.id} onInline={onCamera} showLabel={showLabel} /> : null}
        <OverflowMenu groups={groups} ariaLabel={t("card.tools.more")} />
      </div>
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={t("card.header.deleteTitle", { name: printer.name })}
        description={t("card.header.deleteDesc")}
        confirmLabel={t("card.header.delete")}
        destructive
        onConfirm={() => del.mutate(printer.id)}
      />
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
