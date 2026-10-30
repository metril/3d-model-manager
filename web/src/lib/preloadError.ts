/**
 * After a deploy the service worker drops the old asset cache, so an open tab
 * still running the old build 404s (or gets the SPA fallback HTML) when it
 * lazy-loads a page chunk it hasn't fetched yet. Vite surfaces that as
 * `vite:preloadError`; reload to pick up the new build. A sessionStorage
 * timestamp keeps a genuinely broken deploy from reload-looping.
 */
const KEY = "tdmm:preload-reload-at";
const LOOP_WINDOW_MS = 60_000;

export function installPreloadErrorReload(
  target: Pick<Window, "addEventListener"> = window,
  reload: () => void = () => window.location.reload(),
): void {
  target.addEventListener("vite:preloadError", (event) => {
    let last = 0;
    try {
      last = Number(sessionStorage.getItem(KEY) ?? 0);
    } catch {
      /* storage blocked: fall through and reload once per event */
    }
    if (Date.now() - last < LOOP_WINDOW_MS) return;
    try {
      sessionStorage.setItem(KEY, String(Date.now()));
    } catch {
      /* ignore */
    }
    event.preventDefault();
    reload();
  });
}
