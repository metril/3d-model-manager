/**
 * Viewer background presets + persistence (Workstream A "switchable
 * background"). `resolveBackground` is a pure function so it's trivially
 * unit testable under plain vitest; `useViewerBackground` is the stateful
 * half that persists the choice across sessions and follows the app theme.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useTheme } from "next-themes";

export type BackgroundPreset = "studio" | "white" | "dark" | "theme" | "custom";

/** Order + labels for the picker UI. */
export const BACKGROUND_PRESET_ORDER: readonly BackgroundPreset[] = ["studio", "white", "dark", "theme", "custom"];

export const BACKGROUND_PRESET_LABELS: Record<BackgroundPreset, string> = {
  studio: "Studio",
  white: "White",
  dark: "Dark",
  theme: "Match theme",
  custom: "Custom",
};

// Theme-independent neutral studio background: the page background is
// near-black in dark mode, which hides dark-colored models against a
// transparent canvas. A fixed mid-light cool slate keeps both dark and light
// models legible (tunable) -- this was the previous ModelViewer hard-code.
const STUDIO = "#9aa5b8";
const WHITE = "#ffffff";
const DARK = "#12151c";
// "theme" preset: a mid-dark slate (lighter than `dark` so models keep contrast), and a lighter neutral
// than pure white for the light side so glTF materials don't blow out.
const THEME_DARK = "#252c3a";
const THEME_LIGHT = "#e6e9ef";

export const DEFAULT_CUSTOM_COLOR = STUDIO;

/** Pure preset (+ custom + theme) -> hex resolution. */
export function resolveBackground(preset: BackgroundPreset, custom: string, isDark: boolean): string {
  switch (preset) {
    case "white":
      return WHITE;
    case "dark":
      return DARK;
    case "theme":
      return isDark ? THEME_DARK : THEME_LIGHT;
    case "custom":
      return custom || STUDIO;
    case "studio":
    default:
      return STUDIO;
  }
}

const STORAGE_KEY = "viewer-bg";

interface StoredBackground {
  preset: BackgroundPreset;
  custom: string;
}

const DEFAULT_STATE: StoredBackground = { preset: "theme", custom: DEFAULT_CUSTOM_COLOR };

function isBackgroundPreset(value: unknown): value is BackgroundPreset {
  return typeof value === "string" && (BACKGROUND_PRESET_ORDER as readonly string[]).includes(value);
}

function readStoredBackground(): StoredBackground {
  if (typeof window === "undefined") return DEFAULT_STATE;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_STATE;
    const parsed = JSON.parse(raw) as Partial<StoredBackground> | null;
    return {
      preset: isBackgroundPreset(parsed?.preset) ? parsed.preset : DEFAULT_STATE.preset,
      custom: typeof parsed?.custom === "string" ? parsed.custom : DEFAULT_STATE.custom,
    };
  } catch {
    // Corrupt JSON / disabled storage -- fall back to the default rather
    // than crashing the viewer tab over a persistence nicety.
    return DEFAULT_STATE;
  }
}

function writeStoredBackground(value: StoredBackground): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    // Best-effort only (private browsing / quota exceeded).
  }
}

/** Persists the chosen preset + custom hex in localStorage and resolves the
 * `theme` preset against the app's current `next-themes` theme. Pass `initial`
 * to seed the state directly instead of reading localStorage -- the pop-out
 * window decodes its background from its own URL and must not be clobbered by
 * whatever this tab last stored. An invalid `initial.preset` degrades to the
 * default the same as a corrupt persisted value would. `persist=false` makes
 * this a read/seed-only view: the pop-out window derives its background from
 * the URL and must NOT write it back, or comparing backgrounds in a pop-out
 * would silently change the main tab's saved default. */
export function useViewerBackground(
  initial?: { preset: BackgroundPreset; custom?: string },
  persist = true,
) {
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";

  const [state, setState] = useState<StoredBackground>(() =>
    initial
      ? {
          preset: isBackgroundPreset(initial.preset) ? initial.preset : DEFAULT_STATE.preset,
          custom: initial.custom ?? DEFAULT_STATE.custom,
        }
      : readStoredBackground(),
  );

  // Skip the initial run: `state` was just read back from localStorage, so
  // persisting it again on mount would be a redundant no-op write. Only real
  // changes (via the setters below) should hit storage, and only when this
  // surface is allowed to persist at all.
  const mounted = useRef(false);
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    if (persist) writeStoredBackground(state);
  }, [state, persist]);

  const setPreset = useCallback((preset: BackgroundPreset) => {
    setState((prev) => ({ ...prev, preset }));
  }, []);

  const setCustom = useCallback((custom: string) => {
    setState((prev) => ({ ...prev, custom }));
  }, []);

  return {
    preset: state.preset,
    custom: state.custom,
    color: resolveBackground(state.preset, state.custom, isDark),
    setPreset,
    setCustom,
  };
}
