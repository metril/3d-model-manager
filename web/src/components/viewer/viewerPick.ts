/** Remembers the profile (blob) + plate chosen per model for the browser
 * session, so a remount (studio workspace swap, reload) restores the pick. */
const key = (slug: string) => `viewer-profile:${slug}`;

export interface ViewerPick {
  blob: string;
  plate: number | null;
}

export function loadViewerPick(slug: string): ViewerPick | null {
  try {
    const raw = sessionStorage.getItem(key(slug));
    if (!raw) return null;
    const v = JSON.parse(raw) as { blob?: unknown; plate?: unknown };
    if (typeof v.blob !== "string" || !v.blob) return null;
    const plate = typeof v.plate === "number" && Number.isInteger(v.plate) && v.plate >= 1 ? v.plate : null;
    return { blob: v.blob, plate };
  } catch {
    return null;
  }
}

export function saveViewerPick(slug: string, pick: ViewerPick | null) {
  try {
    if (pick) sessionStorage.setItem(key(slug), JSON.stringify(pick));
    else sessionStorage.removeItem(key(slug));
  } catch {
    /* storage blocked: the pick just isn't remembered */
  }
}
