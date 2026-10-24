import type { PrinterOut, PrinterStatusOut } from "@/api/types";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { modelKey, printerImage } from "@/lib/printerState";

const CAP_LABELS: Record<string, string> = {
  chamber_heater: "Chamber heater",
  chamber_temp: "Chamber temperature",
  airduct: "Airduct",
  door: "Door sensor",
  dual_nozzle: "Dual nozzle",
  aux_fan: "Aux fan",
  chamber_fan: "Chamber fan",
  lidar: "Lidar",
  ams_drying: "AMS drying",
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
  const caps = printer.capabilities;
  const enabledCaps = caps
    ? Object.keys(CAP_LABELS).filter((k) => caps[k as keyof typeof caps] === true)
    : [];
  const rows: [string, string][] = [
    ["Status", status ? (status.online ? (status.gcode_state ?? "online") : "offline") : "unknown"],
    ["IP address", printer.host],
    ["Serial", printer.serial],
    ["Firmware", status?.firmware?.ota ?? "unknown"],
    [
      "Developer mode",
      status?.dev_mode == null ? "unknown" : status.dev_mode ? "on" : "off",
    ],
    ["Nozzle", status?.nozzle_type ?? "unknown"],
    [
      "Total print hours",
      printer.total_print_hours != null ? `${printer.total_print_hours.toFixed(1)} h` : "unknown",
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
            <h3 className="text-sm font-semibold">Capabilities</h3>
            {enabledCaps.length === 0 && caps.camera === "none" ? (
              <p className="text-xs text-muted-foreground">None reported.</p>
            ) : (
              <ul className="flex flex-wrap gap-1.5 text-xs">
                {enabledCaps.map((k) => (
                  <li key={k} className="rounded-full border px-2 py-0.5">
                    {CAP_LABELS[k]}
                  </li>
                ))}
                {caps.camera !== "none" ? (
                  <li className="rounded-full border px-2 py-0.5">Camera ({caps.camera})</li>
                ) : null}
              </ul>
            )}
            {!caps.verified ? (
              <p className="text-xs text-amber-600 dark:text-amber-400">
                untested on this model
              </p>
            ) : null}
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
