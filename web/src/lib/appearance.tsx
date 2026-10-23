/**
 * Q8a appearance: style (classic/glow/vibrant) and accent (6 hues) layered on
 * top of the light/dark/system mode, which stays with `next-themes` (it also
 * feeds sonner and the 3D viewer background). Style/accent live in
 * localStorage and land on <html> as `data-style` / `data-accent`, which the
 * rules in `index.css` key off.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export const STYLES = ["classic", "glow", "vibrant"] as const;
export const ACCENTS = ["orange", "green", "teal", "blue", "purple", "red"] as const;
export type ThemeStyle = (typeof STYLES)[number];
export type ThemeAccent = (typeof ACCENTS)[number];

export const STYLE_KEY = "tdmm-style";
export const ACCENT_KEY = "tdmm-accent";

function read<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = window.localStorage.getItem(key);
    return allowed.includes(value as T) ? (value as T) : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* storage blocked: the choice just won't persist */
  }
}

interface AppearanceValue {
  style: ThemeStyle;
  accent: ThemeAccent;
  setStyle: (style: ThemeStyle) => void;
  setAccent: (accent: ThemeAccent) => void;
}

const AppearanceContext = createContext<AppearanceValue | null>(null);

export function AppearanceProvider({ children }: { children: ReactNode }) {
  const [style, setStyleState] = useState<ThemeStyle>(() => read(STYLE_KEY, STYLES, "classic"));
  const [accent, setAccentState] = useState<ThemeAccent>(() => read(ACCENT_KEY, ACCENTS, "orange"));

  useEffect(() => {
    document.documentElement.dataset.style = style;
    document.documentElement.dataset.accent = accent;
  }, [style, accent]);

  const setStyle = useCallback((next: ThemeStyle) => {
    write(STYLE_KEY, next);
    setStyleState(next);
  }, []);
  const setAccent = useCallback((next: ThemeAccent) => {
    write(ACCENT_KEY, next);
    setAccentState(next);
  }, []);

  const value = useMemo(() => ({ style, accent, setStyle, setAccent }), [style, accent, setStyle, setAccent]);
  return <AppearanceContext.Provider value={value}>{children}</AppearanceContext.Provider>;
}

export function useAppearance(): AppearanceValue {
  const ctx = useContext(AppearanceContext);
  if (!ctx) throw new Error("useAppearance must be used inside AppearanceProvider");
  return ctx;
}
