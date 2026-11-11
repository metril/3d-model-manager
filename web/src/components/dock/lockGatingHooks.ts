import { useEffect } from "react";
import type { DockviewApi } from "dockview-react";

import { useLayoutLocked } from "@/lib/layoutLock";

/** Tab context-menu entries: none while locked (no close/popout/maximize). */
export function tabMenuItems(locked: boolean) {
  return locked ? [] : (["popout", "maximize", "separator", "close"] as const).slice();
}

/** Applies the lock live (no remount): disables drag/drop/split/float and
 * locks every group, re-applying to groups added later. Sash resize stays. */
export function useDockLock(api: DockviewApi | null): boolean {
  const locked = useLayoutLocked();
  useEffect(() => {
    if (!api) return;
    api.updateOptions({ disableDnd: locked });
    const apply = () => {
      for (const g of api.groups) g.locked = locked ? "no-drop-target" : false;
    };
    apply();
    const sub = api.onDidAddGroup(apply);
    return () => sub.dispose();
  }, [api, locked]);
  return locked;
}
