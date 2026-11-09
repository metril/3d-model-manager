import { ApiError } from "@/api/client";

/** Server codes of a direct send refused until the plate is confirmed clear. */
export type PrintHold = "plate_not_cleared" | "printer_failed";

export function asPrintHold(v: unknown): PrintHold | null {
  return v === "plate_not_cleared" || v === "printer_failed" ? v : null;
}

/** The hold behind a 409 from a direct send, if that is what it was. */
export function holdOfError(e: unknown): PrintHold | null {
  return e instanceof ApiError && e.status === 409 ? asPrintHold(e.code) : null;
}
