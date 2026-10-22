/**
 * `/inventory/spools` (P5 Task 9), replacing the P5 placeholder: the spool
 * inventory table, an "Add spool" action (gated on `inventory:write`), a
 * detail `Sheet` (edit form + usage history) opened by a row click, and a
 * "Materials" link back to `/inventory/materials` (materials themselves
 * stayed there, P3). `?low=1` lands with the table's Low stock filter
 * already on, e.g. from a dashboard low-stock badge (Task 10).
 */
import { getRouteApi, Link } from "@tanstack/react-router";
import { useState } from "react";

import { useAuth } from "@/api/auth";
import type { SpoolOut } from "@/api/types";
import { SpoolForm } from "@/components/inventory/SpoolForm";
import { SpoolTable } from "@/components/inventory/SpoolTable";
import { SpoolUsageHistory } from "@/components/inventory/SpoolUsageHistory";
import { useSpool } from "@/api/spools";
import { Button } from "@/components/ui/button";
import { PageContainer } from "@/components/ui/page-container";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";

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

export function SpoolsPage() {
  const { low } = routeApi.useSearch();
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
          {can("inventory:write") ? (
            <Button type="button" onClick={() => setAddOpen(true)}>
              Add spool
            </Button>
          ) : null}
        </div>
      </div>

      <SpoolTable onOpen={(spool: SpoolOut) => setOpenId(spool.id)} initialLowStock={low === "1"} />

      <SpoolDetailSheet spoolId={openId} onOpenChange={(open) => !open && setOpenId(null)} />
      <SpoolForm open={addOpen} onOpenChange={setAddOpen} />
    </PageContainer>
  );
}
