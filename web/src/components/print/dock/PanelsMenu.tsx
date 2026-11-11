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
import { useLayoutLocked } from "@/lib/layoutLock";
import { addPanelAtDefault, buildDefaultLayout } from "./defaultLayout";
import { resetLayout } from "./layoutStore";
import { PANEL_IDS, PANEL_TITLE_KEYS } from "./panelMeta";
import { useTranslation } from "react-i18next";

/** Show/hide each dashboard panel, or reset to the default dock layout. */
export function PanelsMenu({ api }: { api: DockviewApi }) {
  const { t } = useTranslation();
  const locked = useLayoutLocked();
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
        <Button type="button" variant="outline" size="sm">
          <LayoutPanelLeftIcon />
          {t("panelsMenu.panels")}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {PANEL_IDS.map((id) => (
          <DropdownMenuCheckboxItem
            key={id}
            checked={present.has(id)}
            disabled={locked}
            onSelect={(event) => event.preventDefault()}
            onCheckedChange={(checked) => {
              if (checked) addPanelAtDefault(api, id);
              else api.getPanel(id)?.api.close();
            }}
          >
            {t(PANEL_TITLE_KEYS[id])}
          </DropdownMenuCheckboxItem>
        ))}
        <DropdownMenuSeparator />
        {locked ? <div className="px-2 py-1.5 text-xs text-muted-foreground">{t("layoutLock.panelsLocked")}</div> : null}
        <DropdownMenuItem
          disabled={locked}
          onSelect={() => {
            resetLayout();
            buildDefaultLayout(api);
          }}
        >
          {t("panelsMenu.reset")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
