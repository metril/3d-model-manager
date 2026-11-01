import { useCallback, useState } from "react";

import type { SortDir, SortKey } from "@/components/model-detail/explorer/tree";

export type ExplorerLayout = "list" | "grid";

export interface ViewSettings {
  layout: ExplorerLayout;
  sort: SortKey;
  dir: SortDir;
}

export const VIEW_STORAGE_KEY = "tdmm.fileExplorer.view";
const DEFAULTS: ViewSettings = { layout: "list", sort: "name", dir: "asc" };
const SORTS: SortKey[] = ["name", "size", "mtime", "type"];

export function loadViewSettings(): ViewSettings {
  try {
    const raw = JSON.parse(localStorage.getItem(VIEW_STORAGE_KEY) ?? "null") as Partial<ViewSettings> | null;
    return {
      layout: raw?.layout === "grid" ? "grid" : "list",
      sort: SORTS.includes(raw?.sort as SortKey) ? (raw?.sort as SortKey) : DEFAULTS.sort,
      dir: raw?.dir === "desc" ? "desc" : "asc",
    };
  } catch {
    return DEFAULTS;
  }
}

/** Layout/sort settings, persisted across visits (storage failures are non-fatal). */
export function useViewSettings(): [ViewSettings, (patch: Partial<ViewSettings>) => void] {
  const [settings, setSettings] = useState<ViewSettings>(loadViewSettings);
  const update = useCallback((patch: Partial<ViewSettings>) => {
    setSettings((prev) => {
      const next = { ...prev, ...patch };
      try {
        localStorage.setItem(VIEW_STORAGE_KEY, JSON.stringify(next));
      } catch {
        // storage unavailable -- the choice just doesn't persist
      }
      return next;
    });
  }, []);
  return [settings, update];
}
