/** Navigation + dock hand-off for Bambu Studio (usable outside a router context). */

const LOCAL_HOSTS = ["localhost", "127.0.0.1", "[::1]", "::1"];

/** WebCodecs (the Studio stream decoder) needs a secure context. */
export function isInsecureStudioContext(loc: Pick<Location, "protocol" | "hostname"> = window.location): boolean {
  return loc.protocol !== "https:" && !LOCAL_HOSTS.includes(loc.hostname);
}

let dockOpener: (() => void) | null = null;

/** The model-detail dock registers how to reveal its Studio panel; returns the unregister fn. */
export function registerStudioDock(open: () => void): () => void {
  dockOpener = open;
  return () => {
    if (dockOpener === open) dockOpener = null;
  };
}

/** Reveals the Studio dock panel if a dock is mounted, else goes to the full `/studio` page. */
export function showStudio(): void {
  if (dockOpener) dockOpener();
  else void import("@/router").then((m) => m.router.navigate({ to: "/studio" }));
}

export function goToModel(slug: string): void {
  void import("@/router").then((m) => m.router.navigate({ to: "/models/$slug", params: { slug } }));
}
