import { useCallback, useState } from "react";

export type CardSize = "S" | "M" | "L" | "XL";
export const CARD_SIZES: readonly CardSize[] = ["S", "M", "L", "XL"];
export const CARD_SIZE_KEY = "printerCardSize";

/** Body scale per size (CSS var `--pc-s`; `.pc-*` classes in index.css read it). */
export const CARD_SCALE: Record<CardSize, number> = { S: 1, M: 1, L: 1.2, XL: 1.4 };

/** Min card width per size: the dashboard grid is `repeat(auto-fill, minmax(min, 1fr))`. */
export const GRID_MIN: Record<CardSize, number> = { S: 200, M: 340, L: 440, XL: 560 };
export const gridStyle = (size: CardSize) => ({ gridTemplateColumns: `repeat(auto-fill, minmax(min(${GRID_MIN[size]}px, 100%), 1fr))` });

function read(): CardSize | null {
  try {
    const v = localStorage.getItem(CARD_SIZE_KEY);
    return CARD_SIZES.includes(v as CardSize) ? (v as CardSize) : null;
  } catch {
    return null;
  }
}

/** Stored choice wins; otherwise `fallback` (the dashboard passes L for 2 printers or fewer). */
export function useCardSize(fallback: CardSize = "M"): [CardSize, (size: CardSize) => void] {
  const [stored, setStored] = useState<CardSize | null>(read);
  const setSize = useCallback((next: CardSize) => {
    setStored(next);
    try {
      localStorage.setItem(CARD_SIZE_KEY, next);
    } catch {
      // storage unavailable: the choice just doesn't persist
    }
  }, []);
  return [stored ?? fallback, setSize];
}
