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
 * Call synchronously from a user gesture so the browser doesn't block it as a pop-up.
 * Returns `{ win, created }`: `created` is false when a tab from an earlier call was reused,
 * so callers never close a slicer tab they did not open. */
export function openOrcaSlicerWindow(mode: OrcaSlicerOpenMode = readOrcaSlicerMode()): { win: Window | null; created: boolean } {
  try {
    localStorage.setItem(ORCASLICER_MODE_KEY, mode);
  } catch {
    // Storage unavailable: the choice just isn't remembered.
  }
  if (slicerWin && slicerWin.closed === false) {
    slicerWin.focus();
    return { win: slicerWin, created: false };
  }
  const features = `popup,width=${window.screen.availWidth},height=${window.screen.availHeight}`;
  const win = mode === "window" ? window.open("/slicer", WINDOW_NAME, features) : window.open("/slicer", WINDOW_NAME);
  slicerWin = win;
  return { win, created: true };
}

/** Navigates to a model. From the standalone `/slicer` tab, navigates (and focuses) the opener
 * instead of replacing the slicer stream; with no usable opener it opens a new tab. */
export function goToModel(slug: string): void {
  if (window.location.pathname === "/slicer") {
    const url = `/models/${slug}`;
    try {
      const opener = window.opener as Window | null;
      if (opener && !opener.closed && opener.location.origin === window.location.origin) {
        opener.location.assign(url);
        opener.focus();
        return;
      }
    } catch {
      // Cross-origin or detached opener: fall through to a new tab.
    }
    window.open(url, "_blank");
    return;
  }
  void import("@/router").then((m) => m.router.navigate({ to: "/models/$slug", params: { slug } }));
}

/** Test hook: forget the tracked slicer window. */
export function resetOrcaSlicerWindow(): void {
  slicerWin = null;
}
