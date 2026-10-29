/**
 * Client port of backend `slicer_profiles.model_key` / `profile_in_scope`:
 * a profile is in scope when it is universal (no compat/model) or any compat
 * entry has the printer's model key and a nozzle within 0.01 mm (null on
 * either side = any). Keep `MODEL_ALIASES` in sync with the backend table.
 */
import type { SlicerProfile } from "@/api/types";

export const NOZZLE_TOL = 0.01;
export const DEFAULT_NOZZLE = 0.4;

const MODEL_ALIASES: Record<string, string> = {
  x1c: "x1carbon",
  a1m: "a1mini",
  n1: "a1mini",
  n2s: "a1",
  c11: "p1p",
  c12: "p1s",
  c13: "x1e",
  n7: "p2s",
  n6: "x2d",
  n9: "a2l",
  o1d: "h2d",
  o1s: "h2s",
  o1c: "h2c",
  o1c2: "h2c",
  o1e: "h2dpro",
  o2d: "h2dpro",
  blp001: "x1carbon",
  blp002: "x1",
};

function normModel(text: string | null | undefined): string {
  return (text ?? "")
    .toLowerCase()
    .replace(/bambu ?lab/g, "")
    .replace(/[^a-z0-9]/g, "");
}

/** Canonical model key for any spelling ("A1 mini", "Bambu Lab A1 mini", "A1M", "N1" -> "a1mini"). */
export function modelKey(text: string | null | undefined): string | null {
  const norm = normModel(text);
  if (!norm) return null;
  return MODEL_ALIASES[norm] ?? norm;
}

export function nozzleFits(entry: number | null | undefined, nozzle: number | null | undefined): boolean {
  return entry == null || nozzle == null || Math.abs(entry - nozzle) <= NOZZLE_TOL;
}

type ScopeProfile = Pick<SlicerProfile, "compat" | "printer_model_key" | "printer_model">;

function compatEntries(profile: ScopeProfile): { model_key: string; nozzle_mm: number | null }[] | null {
  if (profile.compat && profile.compat.length > 0) return profile.compat;
  const key = profile.printer_model_key ?? null;
  return key ? [{ model_key: key, nozzle_mm: null }] : null;
}

/** True for a profile with no model/compat restriction ("Any printer"). */
export function isUniversal(profile: ScopeProfile): boolean {
  return compatEntries(profile) === null;
}

export function profileInScope(
  profile: ScopeProfile,
  printerKey: string | null,
  nozzle: number | null,
): boolean {
  const entries = compatEntries(profile);
  if (entries === null || printerKey === null) return true;
  return entries.some((e) => e.model_key === printerKey && nozzleFits(e.nozzle_mm, nozzle));
}

/** Why `profile` is out of scope (null when it fits). */
export function scopeMismatchReason(
  profile: ScopeProfile,
  printerKey: string | null,
  nozzle: number | null,
): string | null {
  if (profileInScope(profile, printerKey, nozzle)) return null;
  const entries = compatEntries(profile) ?? [];
  if (!entries.some((e) => e.model_key === printerKey)) {
    return `for ${profile.printer_model ?? entries[0]?.model_key}`;
  }
  const nozzles = [
    ...new Set(entries.filter((e) => e.model_key === printerKey && e.nozzle_mm).map((e) => e.nozzle_mm as number)),
  ].sort((a, b) => a - b);
  return `for ${nozzles.join("/")} mm nozzle`;
}

const MODEL_LABELS: Record<string, string> = {
  a1mini: "A1 mini",
  a1: "A1",
  p1s: "P1S",
  p1p: "P1P",
  x1carbon: "X1 Carbon",
  x1: "X1",
  x1e: "X1E",
  h2d: "H2D",
  h2s: "H2S",
  h2c: "H2C",
  h2dpro: "H2D Pro",
  p2s: "P2S",
  x2d: "X2D",
  a2l: "A2L",
};

export function modelLabel(key: string): string {
  return MODEL_LABELS[key] ?? key.toUpperCase();
}

/** Printer-model cell text: legacy `printer_model`, else labels derived from compat; null = universal. */
export function profileModelText(profile: ScopeProfile): string | null {
  if (profile.printer_model) return profile.printer_model;
  const entries = compatEntries(profile);
  if (!entries) return null;
  return [...new Set(entries.map((e) => e.model_key))].map(modelLabel).join(", ");
}

/** Nozzle cell text: legacy `nozzle_mm`, else compat nozzles ("0.6/0.8 mm"), "any" when unrestricted; null = nothing to show. */
export function profileNozzleText(profile: ScopeProfile & Pick<SlicerProfile, "nozzle_mm">): string | null {
  if (profile.nozzle_mm != null) return `${profile.nozzle_mm} mm`;
  const entries = compatEntries(profile);
  if (!entries) return null;
  const nozzles = [...new Set(entries.map((e) => e.nozzle_mm).filter((n): n is number => n != null))].sort((a, b) => a - b);
  return nozzles.length > 0 ? `${nozzles.join("/")} mm` : "any";
}
