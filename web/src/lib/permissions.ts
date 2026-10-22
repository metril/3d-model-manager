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
  return me.role === "admin" || me.permissions.includes(perm);
}

/** Perm `printers:{kind}` AND per-printer ACL (`printer_access === null` = all printers). */
export function canPrinter(me: Me | undefined, printerId: number, kind: "print" | "control"): boolean {
  if (!me || !can(me, kind === "print" ? "printers:print" : "printers:control")) return false;
  if (me.printer_access === null) return true;
  return me.printer_access[String(printerId)]?.[kind] === true;
}
