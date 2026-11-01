import { useTheme } from "next-themes";
import { useEffect } from "react";

/** next-themes' own key. The legacy shared key `theme` now belongs to the Selkies
 * dashboard (OrcaSlicer stream), which reads it once at mount and writes it on toggle. */
export const APP_THEME_KEY = "tdmm-theme";
export const SELKIES_THEME_KEY = "theme";

type Mode = "dark" | "light";
const isMode = (v: unknown): v is Mode => v === "dark" || v === "light";

/** One-time: carry the old shared `theme` value into the app's own key. Run before render. */
export function migrateThemeKey(): void {
  try {
    if (localStorage.getItem(APP_THEME_KEY) !== null) return;
    const old = localStorage.getItem(SELKIES_THEME_KEY);
    if (old === "dark" || old === "light" || old === "system") localStorage.setItem(APP_THEME_KEY, old);
  } catch {
    /* storage unavailable */
  }
}

/** Mirror the resolved app theme into Selkies' key, and follow Selkies' own toggle
 * (`storage` events fire in this window when the same-origin iframe writes the key). */
export function SelkiesThemeBridge() {
  const { resolvedTheme, setTheme } = useTheme();

  useEffect(() => {
    if (!isMode(resolvedTheme)) return;
    try {
      localStorage.setItem(SELKIES_THEME_KEY, resolvedTheme);
    } catch {
      /* storage unavailable */
    }
  }, [resolvedTheme]);

  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== SELKIES_THEME_KEY || !isMode(e.newValue)) return;
      if (e.newValue !== resolvedTheme) setTheme(e.newValue);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [resolvedTheme, setTheme]);

  return null;
}

/** Make the (same-origin) Selkies dashboard match `theme` by clicking its own toggle, which
 * switches in place without a stream reconnect. Returns "noop" when it already matches,
 * "clicked" after a toggle, and "missing" when the dashboard (or its toggle) isn't there. */
export function syncSelkiesTheme(doc: Document | null | undefined, theme: Mode): "noop" | "clicked" | "missing" {
  const toggle = doc?.querySelector<HTMLElement>(".theme-toggle");
  if (!toggle) return "missing";
  const current: Mode | null = toggle.classList.contains("dark")
    ? "dark"
    : toggle.classList.contains("light")
      ? "light"
      : null;
  if (current === theme) return "noop";
  toggle.click();
  return "clicked";
}
