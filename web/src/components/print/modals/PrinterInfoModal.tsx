import type { PrinterOut, PrinterStatusOut } from "@/api/types";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { modelKey, printerImage } from "@/lib/printerState";
import { useTranslation } from "react-i18next";

const CAP_LABELS: Record<string, string> = {
  chamber_heater: "printerInfo.cap.chamber_heater",
  chamber_temp: "printerInfo.cap.chamber_temp",
  airduct: "printerInfo.cap.airduct",
  door: "printerInfo.cap.door",
  dual_nozzle: "printerInfo.cap.dual_nozzle",
  aux_fan: "printerInfo.cap.aux_fan",
  chamber_fan: "printerInfo.cap.chamber_fan",
  lidar: "printerInfo.cap.lidar",
  ams_drying: "printerInfo.cap.ams_drying",
};

export function PrinterInfoModal({
  printer,
  status,
  open,
  onOpenChange,
}: {
  printer: PrinterOut;
  status?: PrinterStatusOut | null;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const { t } = useTranslation();
  const caps = printer.capabilities;
  const enabledCaps = caps
    ? Object.keys(CAP_LABELS).filter((k) => caps[k as keyof typeof caps] === true)
    : [];
  const rows: [string, string][] = [
    [
      t("printerInfo.status"),
      status
        ? status.online
          ? (status.gcode_state ?? t("printerInfo.online"))
          : t("printerInfo.offline")
        : t("printerInfo.unknown"),
    ],
    [t("printerInfo.ip"), printer.host],
    [t("printerInfo.serial"), printer.serial],
    [t("printerInfo.firmware"), status?.firmware?.ota ?? t("printerInfo.unknown")],
    [
      t("printerInfo.devMode"),
      status?.dev_mode == null ? t("printerInfo.unknown") : status.dev_mode ? t("printerInfo.on") : t("printerInfo.off"),
    ],
    [t("printerInfo.nozzle"), status?.nozzle_type ?? t("printerInfo.unknown")],
    [
      t("printerInfo.totalHours"),
      printer.total_print_hours != null ? `${printer.total_print_hours.toFixed(1)} h` : t("printerInfo.unknown"),
    ],
  ];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{printer.name}</DialogTitle>
        </DialogHeader>
        <div className="flex items-center gap-3">
          <img
            src={printerImage(modelKey(printer.model, printer.serial))}
            alt=""
            className="size-16 object-contain"
          />
          <p className="text-sm text-muted-foreground">{printer.model ?? printer.kind}</p>
        </div>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
          {rows.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted-foreground">{k}</dt>
              <dd className="break-all">{v}</dd>
            </div>
          ))}
        </dl>
        {caps ? (
          <div className="space-y-1.5">
            <h3 className="text-sm font-semibold">{t("printerInfo.capabilities")}</h3>
            {enabledCaps.length === 0 && caps.camera === "none" ? (
              <p className="text-xs text-muted-foreground">{t("printerInfo.noneReported")}</p>
            ) : (
              <ul className="flex flex-wrap gap-1.5 text-xs">
                {enabledCaps.map((k) => (
                  <li key={k} className="rounded-full border px-2 py-0.5">
                    {t(CAP_LABELS[k])}
                  </li>
                ))}
                {caps.camera !== "none" ? (
                  <li className="rounded-full border px-2 py-0.5">{t("printerInfo.camera", { kind: caps.camera })}</li>
                ) : null}
              </ul>
            )}
            {!caps.verified ? (
              <p className="text-xs text-warning">
                {t("printerInfo.untested")}
              </p>
            ) : null}
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
