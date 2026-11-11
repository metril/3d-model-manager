import { DockviewDefaultTab, type IDockviewPanelHeaderProps } from "dockview-react";

import { useLayoutLocked } from "@/lib/layoutLock";

/** Default tab that hides its close button while the layout is locked. */
export function LockableTab(props: IDockviewPanelHeaderProps) {
  const locked = useLayoutLocked();
  return <DockviewDefaultTab {...props} hideClose={locked} />;
}
