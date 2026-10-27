/**
 * Q8a appearance: style (classic/glow/vibrant) and accent (6 hues) layered on
 * top of the light/dark/system mode, which stays with `next-themes` (it also
 * feeds sonner and the 3D viewer background). Style/accent live in
 * localStorage and land on <html> as `data-style` / `data-accent`, which the
 * rules in `index.css` key off.
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import {
  ACCENTS,
  ACCENT_KEY,
  AppearanceContext,
  STYLES,
  STYLE_KEY,
  read,
  write,
  type ThemeAccent,
  type ThemeStyle,
} from "@/lib/appearanceCore";

export function AppearanceProvider({ children }: { children: ReactNode }) {
  const [style, setStyleState] = useState<ThemeStyle>(() => read(STYLE_KEY, STYLES, "classic"));
  const [accent, setAccentState] = useState<ThemeAccent>(() => read(ACCENT_KEY, ACCENTS, "teal"));

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
