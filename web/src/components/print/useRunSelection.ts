import { useState } from "react";

export const MAX_COMPARE = 5;

export interface RunSelection {
  ids: number[];
  toggle: (id: number) => void;
  clear: () => void;
}

export function useRunSelection(max = MAX_COMPARE): RunSelection {
  const [ids, setIds] = useState<number[]>([]);
  return {
    ids,
    toggle: (id) => setIds((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : cur.length >= max ? cur : [...cur, id])),
    clear: () => setIds([]),
  };
}

