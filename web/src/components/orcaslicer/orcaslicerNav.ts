/** Navigation + dock hand-off for OrcaSlicer (usable outside a router context). */

const LOCAL_HOSTS = ["localhost", "127.0.0.1", "[::1]", "::1"];

/** WebCodecs (the OrcaSlicer stream decoder) needs a secure context. */
export function isInsecureOrcaSlicerContext(loc: Pick<Location, "protocol" | "hostname"> = window.location): boolean {
  return loc.protocol !== "https:" && !LOCAL_HOSTS.includes(loc.hostname);
}

let dockOpener: (() => void) | null = null;

/** The model-detail dock registers how to reveal its OrcaSlicer panel; returns the unregister fn. */
export function registerOrcaSlicerDock(open: () => void): () => void {
  dockOpener = open;
  return () => {
    if (dockOpener === open) dockOpener = null;
  };
}

/** Reveals the OrcaSlicer dock panel if a dock is mounted, else goes to the full `/slicer` page. */
export function showOrcaSlicer(): void {
  if (dockOpener) dockOpener();
  else void import("@/router").then((m) => m.router.navigate({ to: "/slicer" }));
}

export function goToModel(slug: string): void {
  void import("@/router").then((m) => m.router.navigate({ to: "/models/$slug", params: { slug } }));
}
