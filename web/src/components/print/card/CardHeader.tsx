/**
 * Card header: model image (printerImage), name link, nozzle size, lifetime
 * print hours, and the single state pill. The printer menu (Edit / Reconnect / Force
 * refresh / Delete) lives in the toolbar's Printer group.
 */
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { PrinterOut, PrinterStatusOut } from "@/api/types";
import { modelKey, printerImage, toneForGcode } from "@/lib/printerState";
import { cn } from "@/lib/utils";

import { StatePill } from "./StatePill";

import { nozzleLabel } from "./nozzleLabel";

export function CardHeader({
  printer,
  status,
  extra,
  imageClassName = "size-10",
}: {
  printer: PrinterOut;
  status: PrinterStatusOut;
  /** `header-extra` slot (Q7 maintenance badge / plug indicator land here). */
  extra?: ReactNode;
  imageClassName?: string;
}) {
  const { t } = useTranslation();
  const nozzle = nozzleLabel(printer, status);
  const hours = printer.total_print_hours;
  const { tone, label } = toneForGcode(status.online, status.gcode_state);
  const model = printer.model && printer.model.trim().toLowerCase() !== printer.name.trim().toLowerCase() ? printer.model : null;

  return (
    <div className="flex items-center gap-3" data-slot="card-header">
      <img
        src={printerImage(modelKey(printer.model, printer.serial))}
        alt=""
        className={cn(imageClassName, "shrink-0 object-contain", tone === "offline" && "grayscale")}
      />
      <div className="min-w-0 flex-1">
        <Link
          to="/printers/$id"
          params={{ id: String(printer.id) }}
          className="[font-size:calc(15px*var(--pc-s,1))] block truncate font-semibold hover:underline"
        >
          {printer.name}
        </Link>
        <p className="pc-t11 truncate text-muted-foreground">
          {[model, nozzle, hours ? t("card.header.hours", { n: Math.round(hours) }) : null]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </div>
      {extra}
      <StatePill tone={tone} label={label} />
    </div>
  );
}
