import { useEffect, useRef, useState } from "react";

const PREFIX = "tdmm.sidebarSection.";

function readStored(key: string): boolean {
  try {
    return window.localStorage.getItem(PREFIX + key) === "1";
  } catch {
    return false;
  }
}

/** Per-section collapsed state for the sidebar, persisted under
 * `tdmm.sidebarSection.<name>` and mirrored across tabs. Modelled on
 * `useSidebarCollapsed` (which owns the whole-sidebar rail state). */
export function useSectionCollapsed(name: string): [boolean, (next: boolean) => void] {
  const [collapsed, setCollapsedState] = useState(() => readStored(name));

  function setCollapsed(next: boolean) {
    setCollapsedState(next);
    try {
      window.localStorage.setItem(PREFIX + name, next ? "1" : "0");
    } catch {
      // Best-effort persistence only (private window / blocked storage).
    }
  }

  useEffect(() => {
    function onStorage(event: StorageEvent) {
      if (event.key === PREFIX + name) setCollapsedState(event.newValue === "1");
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [name]);

  return [collapsed, setCollapsed];
}

/** Auto-expands a collapsed section when navigation lands inside it. Runs
 * only when `locationKey` changes, so collapsing the section you are
 * currently in sticks until you navigate again. */
export function useAutoExpand(
  collapsed: boolean,
  setCollapsed: (next: boolean) => void,
  active: boolean,
  locationKey: string,
) {
  const latest = useRef({ collapsed, setCollapsed, active });
  latest.current = { collapsed, setCollapsed, active };
  useEffect(() => {
    const cur = latest.current;
    if (cur.active && cur.collapsed) cur.setCollapsed(false);
  }, [locationKey]);
}
