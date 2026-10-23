import type { PrinterOut, PrinterStatusOut } from "@/api/types";

export function nozzleLabel(printer: PrinterOut, status: PrinterStatusOut): string | null {
  const dia = printer.options?.nozzle_diameter;
  const size = typeof dia === "number" || typeof dia === "string" ? `${dia} mm` : null;
  const type = status.nozzle_type ? status.nozzle_type.replaceAll("_", " ") : null;
  return [size, type].filter(Boolean).join(" ") || null;
}
