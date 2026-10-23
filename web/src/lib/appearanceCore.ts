import { createContext, useContext } from "react";

export const STYLES = ["classic", "glow", "vibrant"] as const;
export const ACCENTS = ["orange", "green", "teal", "blue", "purple", "red"] as const;
export type ThemeStyle = (typeof STYLES)[number];
export type ThemeAccent = (typeof ACCENTS)[number];

export const STYLE_KEY = "tdmm-style";
export const ACCENT_KEY = "tdmm-accent";

export function read<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = window.localStorage.getItem(key);
    return allowed.includes(value as T) ? (value as T) : fallback;
  } catch {
    return fallback;
  }
}

export function write(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* storage blocked: the choice just won't persist */
  }
}

export interface AppearanceValue {
  style: ThemeStyle;
  accent: ThemeAccent;
  setStyle: (style: ThemeStyle) => void;
  setAccent: (accent: ThemeAccent) => void;
}

export const AppearanceContext = createContext<AppearanceValue | null>(null);

export function useAppearance(): AppearanceValue {
  const ctx = useContext(AppearanceContext);
  if (!ctx) throw new Error("useAppearance must be used inside AppearanceProvider");
  return ctx;
}
