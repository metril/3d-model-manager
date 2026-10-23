import { useCallback, useState } from "react";

export type CardSize = "S" | "M" | "L" | "XL";
export const CARD_SIZES: readonly CardSize[] = ["S", "M", "L", "XL"];
export const CARD_SIZE_KEY = "printerCardSize";

/** Body scale per size (CSS var `--pc-s`; `.pc-*` classes in index.css read it). */
export const CARD_SCALE: Record<CardSize, number> = { S: 1, M: 1, L: 1.2, XL: 1.4 };

/** Grid columns: S 5, M 3, L 2, XL 1 at the widest breakpoint. */
export const GRID_CLASS: Record<CardSize, string> = {
  S: "grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5",
  M: "grid-cols-1 md:grid-cols-2 xl:grid-cols-3",
  L: "grid-cols-1 lg:grid-cols-2",
  XL: "grid-cols-1",
};

function read(): CardSize {
  try {
    const v = localStorage.getItem(CARD_SIZE_KEY);
    return CARD_SIZES.includes(v as CardSize) ? (v as CardSize) : "M";
  } catch {
    return "M";
  }
}

export function useCardSize(): [CardSize, (size: CardSize) => void] {
  const [size, setSizeState] = useState<CardSize>(read);
  const setSize = useCallback((next: CardSize) => {
    setSizeState(next);
    try {
      localStorage.setItem(CARD_SIZE_KEY, next);
    } catch {
      // storage unavailable: the choice just doesn't persist
    }
  }, []);
  return [size, setSize];
}
