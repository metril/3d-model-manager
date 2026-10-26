/**
 * `/inventory/spools` (P5 Task 9), replacing the P5 placeholder: the spool
 * inventory table, an "Add spool" action (gated on `inventory:write`), a
 * detail `Sheet` (edit form + usage history) opened by a row click, and a
 * "Materials" link back to `/inventory/materials` (materials themselves
 * stayed there, P3). `?low=1` lands with the table's Low stock filter
 * already on, e.g. from a dashboard low-stock badge (Task 10).
 */
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useAuth } from "@/api/auth";
import type { SpoolOut } from "@/api/types";
import { ForecastPanel } from "@/components/inventory/ForecastPanel";
import { SpoolForm } from "@/components/inventory/SpoolForm";
import { ColorCatalogPanel } from "@/components/inventory/admin/ColorCatalogPanel";
import { LocationsPanel } from "@/components/inventory/admin/LocationsPanel";
import { SuppliersPanel } from "@/components/inventory/admin/SuppliersPanel";
import { SpoolTable } from "@/components/inventory/SpoolTable";
import { SpoolmanLink } from "@/components/inventory/SpoolmanLink";
import { SpoolUsageHistory } from "@/components/inventory/SpoolUsageHistory";
import { useSpool } from "@/api/spools";
import { Button } from "@/components/ui/button";
import { PageContainer } from "@/components/ui/page-container";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

const routeApi = getRouteApi("/authenticated/inventory/spools");

function SpoolDetailSheet({ spoolId, onOpenChange }: { spoolId: number | null; onOpenChange: (open: boolean) => void }) {
  const spool = useSpool(spoolId);
  const [editing, setEditing] = useState(false);
  const { can } = useAuth();

  return (
    <>
      <Sheet open={spoolId != null} onOpenChange={onOpenChange}>
        <SheetContent side="right" className="sm:max-w-lg">
          <SheetHeader>
            <SheetTitle>
              {spool.data ? `${spool.data.material_type}${spool.data.color_name ? ` · ${spool.data.color_name}` : ""}` : "Spool"}
            </SheetTitle>
            <SheetDescription>{spool.data?.brand ?? "Spool details and usage history"}</SheetDescription>
          </SheetHeader>
          <div className="flex flex-col gap-4 overflow-y-auto px-4 pb-4">
            {spool.isLoading || !spool.data ? (
              <Skeleton className="h-32 w-full rounded-lg" />
            ) : (
              <>
                {can("inventory:write") ? (
                  <Button type="button" size="sm" variant="outline" onClick={() => setEditing(true)}>
                    Edit
                  </Button>
                ) : null}
                <SpoolmanLink spool={spool.data} />
                <SpoolUsageHistory spool={spool.data} />
              </>
            )}
          </div>
        </SheetContent>
      </Sheet>
      {spool.data ? <SpoolForm open={editing} onOpenChange={setEditing} spool={spool.data} /> : null}
    </>
  );
}

const TABS = ["spools", "forecast", "suppliers", "locations", "colors"] as const;
export type SpoolsTab = (typeof TABS)[number];

export function SpoolsPage() {
  const { low, tab: tabParam } = routeApi.useSearch();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const tab: SpoolsTab = TABS.find((x) => x === tabParam) ?? "spools";
  const { can } = useAuth();
  const [openId, setOpenId] = useState<number | null>(null);
  const [addOpen, setAddOpen] = useState(false);

  return (
    <PageContainer width="default">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-foreground">Spools</h1>
          <p className="text-sm text-muted-foreground">Physical filament spools, their AMS slot, and usage.</p>
        </div>
        <div className="flex items-center gap-2">
          <Link to="/inventory/materials" className="text-sm text-muted-foreground hover:underline">
            Materials
          </Link>
          {tab === "spools" && can("inventory:write") ? (
            <Button type="button" onClick={() => setAddOpen(true)}>
              Add spool
            </Button>
          ) : null}
        </div>
      </div>

      <Tabs
        value={tab}
        onValueChange={(v) => void navigate({ to: "/inventory/spools", search: (prev) => ({ ...prev, tab: v === "spools" ? undefined : v }) })}
      >
        <TabsList aria-label={t("spoolAdmin.tabs.label")}>
          {TABS.map((x) => (
            <TabsTrigger key={x} value={x}>
              {t(`spoolAdmin.tabs.${x}`)}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {tab === "spools" ? (
        <SpoolTable onOpen={(spool: SpoolOut) => setOpenId(spool.id)} initialLowStock={low === "1"} />
      ) : tab === "forecast" ? (
        <ForecastPanel />
      ) : tab === "suppliers" ? (
        <SuppliersPanel />
      ) : tab === "locations" ? (
        <LocationsPanel />
      ) : (
        <ColorCatalogPanel />
      )}

      <SpoolDetailSheet spoolId={openId} onOpenChange={(open) => !open && setOpenId(null)} />
      <SpoolForm open={addOpen} onOpenChange={setAddOpen} />
    </PageContainer>
  );
}
