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

let slicerWin: Window | null = null;

/** Opens (or re-focuses) the single named OrcaSlicer tab or window and remembers the choice.
 * It loads the Selkies stream (`/slicer/`) directly, not in an iframe: inside one, Chrome reports
 * `pointerrawupdate` coordinates relative to the top-level page, which offsets pointer motion.
 * Call synchronously from a user gesture so the browser doesn't block it as a pop-up.
 */
export function openOrcaSlicerWindow(mode: OrcaSlicerOpenMode = readOrcaSlicerMode()): void {
  try {
    localStorage.setItem(ORCASLICER_MODE_KEY, mode);
  } catch {
    // Storage unavailable: the choice just isn't remembered.
  }
  if (slicerWin && slicerWin.closed === false) {
    slicerWin.focus();
    return;
  }
  const features = `popup,width=${window.screen.availWidth},height=${window.screen.availHeight}`;
  slicerWin = mode === "window" ? window.open("/slicer/", WINDOW_NAME, features) : window.open("/slicer/", WINDOW_NAME);
}

/** Navigates to a model in the app router. */
export function goToModel(slug: string): void {
  void import("@/router").then((m) => m.router.navigate({ to: "/models/$slug", params: { slug } }));
}

/** Test hook: forget the tracked slicer window. */
export function resetOrcaSlicerWindow(): void {
  slicerWin = null;
}
