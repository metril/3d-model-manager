import { createContext, useContext } from "react";

/** Where Radix overlays (menus, popovers, dialogs, tooltips, selects) should
 * portal. `undefined` = Radix's default (the main `document.body`). A dock
 * panel provides its own document's body so overlays opened from a panel
 * living in a pop-out window render in that window, not behind it. */
export const PortalContainerContext = createContext<HTMLElement | undefined>(undefined);

export function usePortalContainer(): HTMLElement | undefined {
  return useContext(PortalContainerContext);
}
