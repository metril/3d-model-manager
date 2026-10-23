import type { PrinterCapabilities, PrinterOut } from "@/api/types";

/** Capabilities assumed when the payload predates Q1: nothing optional, no "untested" noise. */
export const NO_CAPS: PrinterCapabilities = {
  model_key: null,
  chamber_heater: false,
  chamber_temp: false,
  airduct: false,
  door: false,
  dual_nozzle: false,
  aux_fan: false,
  chamber_fan: false,
  camera: "none",
  lidar: false,
  ams_drying: false,
  verified: true,
};

export function capsOf(printer: PrinterOut): PrinterCapabilities {
  return printer.capabilities ?? NO_CAPS;
}

/** States in which motion/extrusion/calibration are refused (mirrors the backend guard + PREPARE). */
const BUSY = new Set(["RUNNING", "PAUSE", "PREPARE"]);

export function isBusy(gcodeState: string | null | undefined): boolean {
  return BUSY.has(gcodeState ?? "");
}

export const UNTESTED_TIP = "Untested on this model";
