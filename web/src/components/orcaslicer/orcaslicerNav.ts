/** Navigation helpers for OrcaSlicer (usable outside a router context). */

const LOCAL_HOSTS = ["localhost", "127.0.0.1", "[::1]", "::1"];

/** WebCodecs (the OrcaSlicer stream decoder) needs a secure context. */
export function isInsecureOrcaSlicerContext(loc: Pick<Location, "protocol" | "hostname"> = window.location): boolean {
  return loc.protocol !== "https:" && !LOCAL_HOSTS.includes(loc.hostname);
}

export type OrcaSlicerOpenMode = "tab" | "window";

export const ORCASLICER_MODE_KEY = "tdmm.orcaslicer.openMode";
const WINDOW_NAME = "tdmm-orcaslicer";

/** The last tab/window choice (default `tab`). */
export function readOrcaSlicerMode(): OrcaSlicerOpenMode {
  try {
    return localStorage.getItem(ORCASLICER_MODE_KEY) === "window" ? "window" : "tab";
  } catch {
    return "tab";
  }
}

/** Opens (or re-focuses) the single named OrcaSlicer tab or window and remembers the choice.
 * Call synchronously from a user gesture so the browser doesn't block it as a pop-up. */
export function openOrcaSlicerWindow(mode: OrcaSlicerOpenMode = readOrcaSlicerMode()): Window | null {
  try {
    localStorage.setItem(ORCASLICER_MODE_KEY, mode);
  } catch {
    // Storage unavailable: the choice just isn't remembered.
  }
  if (mode === "window") {
    const features = `popup,width=${window.screen.availWidth},height=${window.screen.availHeight}`;
    return window.open("/slicer", WINDOW_NAME, features);
  }
  return window.open("/slicer", WINDOW_NAME);
}

export function goToModel(slug: string): void {
  void import("@/router").then((m) => m.router.navigate({ to: "/models/$slug", params: { slug } }));
}
