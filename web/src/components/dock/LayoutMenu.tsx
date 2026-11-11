import { useEffect, useState, type ReactNode } from "react";
import type { DockviewApi } from "dockview-react";
import { LayoutPanelLeftIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { setLayoutLocked } from "@/lib/layoutLock";

export interface LayoutMenuPanels {
  api: DockviewApi;
  ids: readonly string[];
  titleKeys: Record<string, string>;
  add: (api: DockviewApi, id: never) => void;
}

/** One "Layout" menu for docks and the stats grid: rearrange (unlocks), show/hide panels, reset. */
export function LayoutMenu({
  panels,
  onReset,
  children,
}: {
  panels?: LayoutMenuPanels;
  onReset: () => void;
  /** Extra items (e.g. a view-mode radio group), rendered above the layout items. */
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  const api = panels?.api;
  const [present, setPresent] = useState(() => new Set(api?.panels.map((p) => p.id)));

  useEffect(() => {
    if (!api) return;
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
          {t("layout.menu")}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {children}
        {children ? <DropdownMenuSeparator /> : null}
        <DropdownMenuItem onSelect={() => setLayoutLocked(false)}>{t("layout.rearrange")}</DropdownMenuItem>
        {panels ? <DropdownMenuSeparator /> : null}
        {panels?.ids.map((id) => (
          <DropdownMenuCheckboxItem
            key={id}
            checked={present.has(id)}
            onSelect={(event) => event.preventDefault()}
            onCheckedChange={(checked) => {
              if (checked) panels.add(panels.api, id as never);
              else panels.api.getPanel(id)?.api.close();
            }}
          >
            {t(panels.titleKeys[id])}
          </DropdownMenuCheckboxItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={onReset}>{t("layout.reset")}</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
