import { useCallback, useMemo, useState } from "react";

/** Selection keys are prefixed so folders can join files in PR 4 (rename,
 * move, bulk delete) without a second state container. */
export const fileKey = (id: number) => `file:${id}`;
export const folderKey = (path: string) => `folder:${path}`;

export interface ExplorerSelection {
  keys: ReadonlySet<string>;
  has: (key: string) => boolean;
  toggle: (key: string, on: boolean) => void;
  /** Drops every selected key not in `visible` (no-op if nothing to drop). */
  retain: (visible: ReadonlySet<string>) => void;
  clear: () => void;
}

export function useSelection(): ExplorerSelection {
  const [keys, setKeys] = useState<ReadonlySet<string>>(new Set());
  const toggle = useCallback((key: string, on: boolean) => {
    setKeys((prev) => {
      const next = new Set(prev);
      if (on) next.add(key);
      else next.delete(key);
      return next;
    });
  }, []);
  const retain = useCallback((visible: ReadonlySet<string>) => {
    setKeys((prev) => {
      const next = new Set([...prev].filter((key) => visible.has(key)));
      return next.size === prev.size ? prev : next;
    });
  }, []);
  const clear = useCallback(() => setKeys((prev) => (prev.size === 0 ? prev : new Set())), []);
  return useMemo(() => ({ keys, has: (key) => keys.has(key), toggle, retain, clear }), [keys, toggle, retain, clear]);
}
