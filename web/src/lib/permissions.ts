import type { Me } from "@/api/auth";

export type Perm =
  | "admin"
  | "library:write"
  | "inventory:write"
  | "printers:print"
  | "printers:control"
  | "queue:review"
  | "settings:write"
  | "billing:manage";

/** Whether `me` holds `perm` (admins hold `admin` plus whatever the server resolved). */
export function can(me: Me | undefined, perm: Perm): boolean {
  if (!me) return false;
  return me.role === "admin" || !!me.permissions?.includes(perm);
}

/** Perm `printers:{kind}` AND per-printer ACL (`printer_access === null` = all printers). */
export function canPrinter(me: Me | undefined, printerId: number, kind: "print" | "control"): boolean {
  if (!me || !can(me, kind === "print" ? "printers:print" : "printers:control")) return false;
  if (me.printer_access === null) return true;
  return me.printer_access[String(printerId)]?.[kind] === true;
}

/** Whether a print `me` is about to send would need review (P7 review/
 * billing plan): an admin, or anyone who can review their own work
 * (`queue:review`), never needs it -- mirrors the server's own
 * `app.services.review.needs_review`. `printerId` is the chosen target, or
 * `null` when none is picked yet (fans out over every printer in `me`'s own
 * ACL that flags `requires_review`). This is a client-side best-effort
 * hint only -- `PrintDialog` falls back to the server's 409 `queue_required`
 * either way. */
export function needsReview(me: Me | undefined, printerId: number | null): boolean {
  if (!me) return false;
  if (me.role === "admin" || can(me, "queue:review")) return false;
  if (!me.printer_access) return false;
  if (printerId != null) {
    return me.printer_access[String(printerId)]?.requires_review === true;
  }
  return Object.values(me.printer_access).some((flags) => flags.requires_review === true);
}
