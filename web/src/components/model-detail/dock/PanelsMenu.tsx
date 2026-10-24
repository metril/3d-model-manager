import { useEffect, useState } from "react";
import type { DockviewApi } from "dockview-react";
import { LayoutPanelLeftIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { addPanelAtDefault, buildDefaultLayout } from "./defaultLayout";
import { resetLayout } from "./layoutStore";
import { PANEL_IDS, PANEL_TITLES } from "./panelMeta";

/** Header dropdown: show/hide each registry panel, or reset to the default layout. */
export function PanelsMenu({ api }: { api: DockviewApi }) {
  const [present, setPresent] = useState(() => new Set(api.panels.map((p) => p.id)));

  useEffect(() => {
    const sync = () => setPresent(new Set(api.panels.map((p) => p.id)));
    sync();
    const subs = [api.onDidAddPanel(sync), api.onDidRemovePanel(sync), api.onDidLayoutFromJSON(sync)];
    return () => subs.forEach((sub) => sub.dispose());
  }, [api]);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button type="button" variant="outline">
          <LayoutPanelLeftIcon />
          Panels
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {PANEL_IDS.map((id) => (
          <DropdownMenuCheckboxItem
            key={id}
            checked={present.has(id)}
            onSelect={(event) => event.preventDefault()}
            onCheckedChange={(checked) => {
              if (checked) addPanelAtDefault(api, id);
              else api.getPanel(id)?.api.close();
            }}
          >
            {PANEL_TITLES[id]}
          </DropdownMenuCheckboxItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={() => {
            resetLayout();
            buildDefaultLayout(api);
          }}
        >
          Reset layout
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
