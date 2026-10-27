import { useEffect, useState } from "react";
import { getRouteApi } from "@tanstack/react-router";

import { usePendingImports } from "@/api/collections";
import { ImportsPanel } from "@/components/collections/ImportsPanel";
import { BrowseCard, FollowedCard, ReviewQueueCard } from "@/components/collections/SavedPanel";
import { Badge } from "@/components/ui/badge";
import { PageContainer } from "@/components/ui/page-container";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { CollectionsTab } from "@/pages/collectionsSearch";

const routeApi = getRouteApi("/authenticated/collections");

/** Top-level home for saved-collection sync. This lived as a "Saved" tab inside
 * `/add` until users repeatedly failed to find it: nothing in the nav said
 * "collection" or "sync", so following a MakerWorld list meant clicking a button
 * labelled "Add to library" (which reads as "import one model now") and then
 * spotting a fourth tab. Following remote lists is ongoing, recurring work --
 * not a one-off add -- so it gets its own rail entry and route.
 *
 * R7 T2: with the review queue now able to hold 100+ items, stacking all
 * three cards on one page pushed "Followed collections" and "Recent
 * imports" out of view -- three tabs instead. `usePendingImports()` here
 * shares its React Query cache with `ReviewQueueCard`'s own call (same
 * `["collections", "pending"]` key), so this is not a second network
 * request; it only needs the count for the default-tab rule and the trigger
 * badge.
 *
 * R7 fix: the no-`?tab=` default is resolved ONCE, the first time the
 * pending query settles, and then pinned in state. Without this, a cold
 * load flashes Collections before flipping to Review once the count
 * resolves, and a later background refetch (e.g. window refocus after a
 * sync surfaces new review rows) can yank the user off a tab they're
 * already looking at. While the query is still loading with no `?tab=`,
 * the tabs render a stable provisional "collections" selection instead of
 * flipping. An explicit tab click always writes `?tab=`, which wins over
 * the pinned default regardless of its value. */
export function CollectionsPage() {
  const search = routeApi.useSearch();
  const navigate = routeApi.useNavigate();
  const pending = usePendingImports();
  const pendingCount = pending.data?.length ?? 0;

  const [pinnedDefaultTab, setPinnedDefaultTab] = useState<CollectionsTab | null>(null);
  useEffect(() => {
    if (pinnedDefaultTab !== null || pending.isLoading) return;
    setPinnedDefaultTab(pendingCount > 0 ? "review" : "collections");
  }, [pinnedDefaultTab, pending.isLoading, pendingCount]);

  const activeTab = search.tab ?? pinnedDefaultTab ?? "collections";

  function handleTabChange(value: string) {
    void navigate({
      search: (prev) => ({ ...prev, tab: value as CollectionsTab }),
      replace: true,
    });
  }

  return (
    <PageContainer width="default">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">Collections</h1>
        <p className="text-sm text-muted-foreground">
          Follow saved lists from MakerWorld, Thingiverse, and Printables, and keep them synced with
          your library.
        </p>
      </div>

      <Tabs value={activeTab} onValueChange={handleTabChange}>
        <TabsList>
          <TabsTrigger value="review" className="gap-1.5">
            Review queue
            {pendingCount > 0 && (
              <Badge variant="secondary" className="font-mono">
                {pendingCount}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="collections">Collections</TabsTrigger>
          <TabsTrigger value="imports">Recent imports</TabsTrigger>
        </TabsList>
        <TabsContent value="review" className="space-y-6">
          <ReviewQueueCard />
        </TabsContent>
        <TabsContent value="collections" className="space-y-6">
          <FollowedCard />
          <BrowseCard />
        </TabsContent>
        <TabsContent value="imports" className="space-y-6">
          <ImportsPanel />
        </TabsContent>
      </Tabs>
    </PageContainer>
  );
}
