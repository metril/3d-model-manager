import type { ImportSite } from "@/api/types";

export interface DetectedSite {
  site: ImportSite | null;
  label: string;
  supported: boolean; // all three known sites are supported; false only for an unknown host
}

const HOSTS: Record<string, { site: ImportSite; label: string; supported: boolean }> = {
  "thingiverse.com": { site: "thingiverse", label: "Thingiverse", supported: true },
  "printables.com": { site: "printables", label: "Printables", supported: true },
  "makerworld.com": { site: "makerworld", label: "MakerWorld", supported: true },
};

/** Client-side site detection for the /import preview. All three sites are
 * recognized and supported (MakerWorld as of Workstream B); anything else is
 * unknown. Mirrors the backend's registry. */
export function detectSite(rawUrl: string): DetectedSite {
  let host = "";
  try {
    host = new URL(rawUrl.trim()).hostname.toLowerCase().replace(/^www\./, "");
  } catch {
    return { site: null, label: "", supported: false };
  }
  const hit = HOSTS[host];
  return hit ? { ...hit } : { site: null, label: "", supported: false };
}

const SITE_LABELS: Record<string, string> = Object.fromEntries(
  Object.values(HOSTS).map((h) => [h.site, h.label]),
);

/** Display name for a stored site slug ("makerworld" -> "MakerWorld"); unknown slugs pass through. */
export function siteLabel(site: string): string {
  return SITE_LABELS[site] ?? site;
}
