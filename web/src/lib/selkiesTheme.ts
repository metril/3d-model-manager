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

/** The app's resolved theme computed from storage + the OS, not from a possibly stale closure. */
export function freshResolvedTheme(): Mode {
  try {
    const stored = localStorage.getItem(APP_THEME_KEY);
    if (isMode(stored)) return stored;
  } catch {
    /* storage unavailable */
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Mirror the resolved app theme into Selkies' key (so the stream reads it at mount). */
export function useMirrorSelkiesTheme(): void {
  const { resolvedTheme } = useTheme();
  useEffect(() => {
    if (!isMode(resolvedTheme)) return;
    try {
      localStorage.setItem(SELKIES_THEME_KEY, resolvedTheme);
    } catch {
      /* storage unavailable */
    }
  }, [resolvedTheme]);
}

/** Follow Selkies' own toggle (a `storage` event on `theme` fires here when the stream tab
 * writes it). The no-op check against the resolved theme keeps a `system` preference intact.
 * The other direction (app to stream) is `tdmm-theme-sync.js`, injected into the Selkies page. */
export function useFollowSelkiesTheme(): void {
  const { setTheme } = useTheme();
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== SELKIES_THEME_KEY || !isMode(e.newValue)) return;
      if (e.newValue !== freshResolvedTheme()) setTheme(e.newValue);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [setTheme]);
}
