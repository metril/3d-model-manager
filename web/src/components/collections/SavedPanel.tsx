import { type FormEvent, useState } from "react";
import { EntityTags } from "@/components/tags/EntityTags";
import { TagFilter } from "@/components/tags/TagFilter";
import { EMPTY_TAG_FILTER, type TagFilterValue } from "@/components/tags/tagFilter";
import { Link } from "@tanstack/react-router";
import { ChevronDownIcon, ChevronRightIcon, ImageIcon, RefreshCwIcon } from "lucide-react";

import { ApiError } from "@/api/client";
import {
  useApprovePending,
  useDismissPending,
  useFollowCollection,
  useFollowCollectionByUrl,
  useFollowedCollections,
  usePendingImports,
  useRemoteLists,
  useSetCollectionMode,
  useSyncCollectionsNow,
  useUnfollowCollection,
} from "@/api/collections";
import type { CollectionSyncMode, FollowedCollection, PendingImport, RemoteList } from "@/api/types";
import { ExpandCollapseAll } from "@/components/ExpandCollapseAll";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDate } from "@/lib/format";
import { useOpenMap } from "@/lib/useOpenMap";

/** "Saved" surface (M8 H): the remote collections/likes you follow, what a sync
 * should do with each (auto-import vs review), the review queue, and a manual
 * "Sync now". Browsing a site's lists needs that site's credential connected in
 * Settings (MakerWorld web token / Thingiverse App Token / Printables account);
 * a site without one contributes nothing, hence the explicit empty state.
 *
 * R7 T2: `CollectionsPage` used to stack all three cards below; the page is
 * now three tabs, so each card is its own export placed independently
 * (Followed + Browse under "Collections", the queue under its own "Review
 * queue" tab) instead of one `SavedPanel` wrapper. Each card fetches its own
 * data via React Query, which dedupes by query key, so calling e.g.
 * `useFollowedCollections()` from both `FollowedCard` and `BrowseCard` is a
 * single shared request, not two. */
export function FollowedCard() {
  const [tagFilter, setTagFilter] = useState<TagFilterValue>(EMPTY_TAG_FILTER);
  const followed = useFollowedCollections({
    tags: tagFilter.tags.length ? tagFilter.tags : undefined,
    tagMode: tagFilter.tags.length > 1 ? tagFilter.mode : undefined,
  });
  const syncNow = useSyncCollectionsNow();

  return (
    <Card>
      <CardHeader>
        <CardTitle>Followed collections</CardTitle>
        <CardDescription>
          Lists you follow are re-checked on each sync. New models are imported straight away
          (auto) or queued for you to approve (review).
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            disabled={syncNow.isPending}
            onClick={() => syncNow.mutate()}
          >
            <RefreshCwIcon />
            {syncNow.isPending ? "Syncing…" : "Sync now"}
          </Button>
          <TagFilter value={tagFilter} onChange={setTagFilter} countKey="collections" />
          {syncNow.isError && (
            <p role="alert" className="text-sm text-destructive">
              {syncNow.error instanceof ApiError ? syncNow.error.detail : "Could not start the sync."}
            </p>
          )}
        </div>

        {followed.isLoading ? (
          <Skeleton className="h-20 w-full rounded-lg" />
        ) : (followed.data ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {tagFilter.tags.length > 0
              ? "No followed collections match this tag filter."
              : "You aren't following any collections yet."}
          </p>
        ) : (
          <div className="space-y-2">
            {(followed.data ?? []).map((collection) => (
              <FollowedRow key={collection.id} collection={collection} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** 2x2 collage of the collection's first (most-recently-imported) 4 member
 * models, in place of a single cover (R11-C item 17). Fewer than 4 previews
 * leaves the remaining cells as an empty placeholder tile rather than
 * stretching what's there. */
function CollectionCollage({ thumbnails }: { thumbnails: string[] }) {
  return (
    <div className="grid size-12 shrink-0 grid-cols-2 grid-rows-2 gap-0.5 overflow-hidden rounded-md bg-muted">
      {Array.from({ length: 4 }, (_, i) => thumbnails[i]).map((url, i) =>
        url ? (
          <img
            key={i}
            src={url}
            alt=""
            data-testid="collage-thumb"
            loading="lazy"
            className="h-full w-full object-cover"
          />
        ) : (
          <div key={i} className="bg-muted" />
        ),
      )}
    </div>
  );
}

function FollowedRow({ collection }: { collection: FollowedCollection }) {
  const setMode = useSetCollectionMode();
  const unfollow = useUnfollowCollection();

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border px-3 py-2">
      <CollectionCollage thumbnails={collection.preview_thumbnails} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-foreground">{collection.title}</p>
        <EntityTags target={{ kind: "collection", id: collection.id }} tags={collection.tag_refs} className="my-0.5" />
        <p className="font-mono text-xs text-muted-foreground">
          <span className="capitalize">{collection.site}</span> · {collection.kind} ·{" "}
          {collection.last_synced_at ? `synced ${formatDate(collection.last_synced_at)}` : "never synced"}
        </p>
        {collection.last_error && (
          <p role="alert" className="text-xs text-destructive">
            {collection.last_error}
          </p>
        )}
      </div>
      <Select
        value={collection.mode}
        onValueChange={(mode) => setMode.mutate({ id: collection.id, mode: mode as CollectionSyncMode })}
      >
        <SelectTrigger aria-label={`Sync mode for ${collection.title}`} size="sm" className="w-36">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="auto">Auto-import</SelectItem>
          <SelectItem value="review">Review</SelectItem>
        </SelectContent>
      </Select>
      {/* Plain anchor with `download` -- the session cookie carries auth, so
          no fetch-and-blob dance is needed (R11-A). */}
      <Button type="button" variant="outline" size="sm" asChild>
        <a href={`/api/collections/${collection.id}/zip`} download aria-label={`Download ${collection.title} as zip`}>
          Download ZIP
        </a>
      </Button>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={unfollow.isPending}
        onClick={() => unfollow.mutate(collection.id)}
      >
        Unfollow
      </Button>
    </div>
  );
}

interface PendingGroup {
  groupCollectionId: number;
  title: string;
  items: PendingImport[];
}

/** Groups pending items by the server-resolved `group_collection_id`/
 * `group_title` (R7 T1: `GET /collections/pending` now carries both, so this
 * no longer joins against `useFollowedCollections()` client-side -- the
 * backend already resolves real membership, including cases where two items
 * share a legacy `collection_id` but belong to different display groups). A
 * missing/empty `group_title` -- shouldn't happen given the field is
 * required, but a defensive fallback costs nothing -- reads as
 * `Collection #<id>` instead of a blank header. Groups sort by title so the
 * section order is stable; items keep the API's own order within a group. */
function groupPendingItems(items: PendingImport[]): PendingGroup[] {
  const groupOrder: number[] = [];
  const byGroup = new Map<number, PendingImport[]>();
  const titleByGroup = new Map<number, string>();
  for (const item of items) {
    const key = item.group_collection_id;
    const existing = byGroup.get(key);
    if (existing) {
      existing.push(item);
    } else {
      byGroup.set(key, [item]);
      groupOrder.push(key);
      titleByGroup.set(key, item.group_title || `Collection #${key}`);
    }
  }
  return groupOrder
    .map((groupCollectionId) => ({
      groupCollectionId,
      title: titleByGroup.get(groupCollectionId) ?? `Collection #${groupCollectionId}`,
      items: byGroup.get(groupCollectionId) ?? [],
    }))
    .sort((a, b) => a.title.localeCompare(b.title));
}

/** The queue's own tab (R7 T2) already gives the whole card room to breathe,
 * so the card itself keeps a static header (title + count, plus an R11
 * expand/collapse-all control once groups exist) -- only each GROUP inside
 * it collapses now, independently, via the shared `useOpenMap` hook (no
 * localStorage; per-tab session state is enough). Every group defaults
 * OPEN, so an id absent from the map (never toggled) reads as open. */
export function ReviewQueueCard() {
  const pending = usePendingImports();
  // `items`/`groups` are computed up front, before the loading/empty early
  // returns below, so the `useOpenMap` call itself stays unconditional --
  // `groups` is simply `[]` while loading or empty, which the hook handles
  // fine.
  const items = pending.data ?? [];
  const groups = groupPendingItems(items);
  const { isOpen, toggle, openAll, closeAll, allOpen, allClosed } = useOpenMap(
    groups.map((group) => group.groupCollectionId),
    true,
  );

  if (pending.isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Review queue</CardTitle>
          <CardDescription>
            New models found in your <em>review</em> lists. Nothing enters the library until you
            import it.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-24 w-full rounded-lg" />
        </CardContent>
      </Card>
    );
  }

  if (items.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Review queue</CardTitle>
          <CardDescription>
            New models found in your <em>review</em> lists. Nothing enters the library until you
            import it.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">Nothing waiting for review.</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <CardTitle className="flex-1">Review queue</CardTitle>
          <Badge variant="secondary" className="font-mono">
            {items.length}
          </Badge>
          <ExpandCollapseAll
            label="review groups"
            allOpen={allOpen}
            allClosed={allClosed}
            onExpandAll={openAll}
            onCollapseAll={closeAll}
          />
        </div>
        <CardDescription>
          New models found in your <em>review</em> lists. Nothing enters the library until you
          import it.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="space-y-5" data-testid="review-queue">
          {groups.map((group) => (
            <ReviewGroup
              key={group.groupCollectionId}
              group={group}
              open={isOpen(group.groupCollectionId)}
              onToggle={() => toggle(group.groupCollectionId)}
            />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function ReviewGroup({
  group,
  open,
  onToggle,
}: {
  group: PendingGroup;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <div>
      <button
        type="button"
        className="mb-2 flex w-full items-center gap-2 text-left"
        aria-expanded={open}
        onClick={onToggle}
      >
        {open ? (
          <ChevronDownIcon className="size-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRightIcon className="size-4 shrink-0 text-muted-foreground" />
        )}
        <h3 className="text-sm font-medium text-foreground">
          {group.title} <span className="font-normal text-muted-foreground">· {group.items.length}</span>
        </h3>
      </button>
      {open && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {group.items.map((item) => (
            <ReviewCard key={item.id} item={item} />
          ))}
        </div>
      )}
    </div>
  );
}

/** Fixed-shape flex column so Import/Dismiss line up across every card in a
 * row regardless of title length: `h-full` lets the card fill the grid
 * row's stretched height, the thumbnail keeps its own `aspect-square` (never
 * grows/shrinks), and `CardContent` becomes the flexible remainder --
 * `flex-1` so it absorbs whatever height the thumbnail didn't use, and
 * `mt-auto` on the actions row pushes the buttons to the bottom of that
 * remainder. The clamped title reserves a 2-line box (`min-h-10` == two
 * `text-sm`/`leading-5` lines) so a 1-line title doesn't shrink the card and
 * a 3-line title doesn't grow it -- same fixed-height-then-clamp idiom as
 * `ModelCard`'s tag row, just for text instead of tags. */
function ReviewCard({ item }: { item: PendingImport }) {
  const approve = useApprovePending();
  const dismiss = useDismissPending();

  return (
    <Card className="flex h-full flex-col gap-2 overflow-hidden" size="sm">
      <div className="relative flex aspect-square shrink-0 items-center justify-center bg-muted">
        {item.thumbnail_url ? (
          <img src={item.thumbnail_url} alt={item.title} className="h-full w-full object-cover" />
        ) : (
          <ImageIcon className="size-8 text-muted-foreground" />
        )}
        <Badge variant="secondary" className="absolute top-1.5 left-1.5 capitalize backdrop-blur-sm">
          {item.site}
        </Badge>
      </div>
      <CardContent className="flex flex-1 flex-col gap-1.5">
        <h4 className="line-clamp-2 min-h-10 text-sm leading-5 font-medium" title={item.title}>
          {item.title}
        </h4>
        <div className="mt-auto flex gap-1.5" data-testid="review-item-actions">
          <Button
            type="button"
            size="sm"
            disabled={approve.isPending}
            onClick={() => approve.mutate(item.id)}
          >
            Import
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={dismiss.isPending}
            onClick={() => dismiss.mutate(item.id)}
          >
            Dismiss
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

/** MakerWorld's own SSR collections page is intermittently Cloudflare-walled,
 * so `useRemoteLists` can come back without a collection the user actually
 * has -- pasting its URL follows it directly (`POST /collections/from-url`)
 * without needing it to show up in the browsable list first. */
function AddCollectionByUrl() {
  const [url, setUrl] = useState("");
  const followByUrl = useFollowCollectionByUrl();

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = url.trim();
    if (!trimmed || followByUrl.isPending) {
      return;
    }
    followByUrl.mutate({ url: trimmed }, { onSuccess: () => setUrl("") });
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-2" data-testid="add-collection-by-url">
      <div className="flex flex-wrap items-center gap-2">
        <Label htmlFor="collection-url" className="sr-only">
          Collection URL
        </Label>
        <Input
          id="collection-url"
          type="url"
          placeholder="https://makerworld.com/…/collections/…"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          className="h-8 max-w-sm flex-1"
        />
        <Button type="submit" size="sm" variant="outline" disabled={!url.trim() || followByUrl.isPending}>
          {followByUrl.isPending ? "Following…" : "Follow"}
        </Button>
      </div>
      {followByUrl.isError && (
        <p role="alert" className="text-sm text-destructive">
          {followByUrl.error instanceof ApiError ? followByUrl.error.detail : "Could not follow that collection."}
        </p>
      )}
    </form>
  );
}

export function BrowseCard() {
  const lists = useRemoteLists();
  const follow = useFollowCollection();
  const followed = useFollowedCollections();
  const followedKeys = new Set((followed.data ?? []).map((c) => `${c.site}:${c.list_id}`));

  return (
    <Card>
      <CardHeader>
        <CardTitle>Your collections on each site</CardTitle>
        <CardDescription>Follow a list to keep it synced with your library.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <AddCollectionByUrl />
        {lists.isLoading ? (
          <Skeleton className="h-20 w-full rounded-lg" />
        ) : (lists.data ?? []).length === 0 ? (
          <div className="space-y-2 text-sm text-muted-foreground">
            <p>
              No collections found yet. To sync your saved models, connect each site in{" "}
              <Link to="/settings" className="underline">
                Settings
              </Link>{" "}
              → Imports:
            </p>
            <ul className="list-disc space-y-1 pl-5">
              <li>
                <span className="font-medium text-foreground">MakerWorld</span> — paste your web
                token under Gallery site tokens. Your MakerWorld collections sync from the browser
                extension — open your MakerWorld collections page once with the extension
                installed.
              </li>
              <li>
                <span className="font-medium text-foreground">Thingiverse</span> — paste your App
                Token under Gallery site tokens.
              </li>
              <li>
                <span className="font-medium text-foreground">Printables</span> — connect your
                account.
              </li>
            </ul>
          </div>
        ) : (
          <div className="space-y-2" data-testid="remote-lists">
            {(lists.data ?? []).map((list: RemoteList) => {
              const already = followedKeys.has(`${list.site}:${list.list_id}`);
              return (
                <div
                  key={`${list.site}:${list.list_id}`}
                  className="flex flex-wrap items-center gap-3 rounded-lg border border-border px-3 py-2"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-foreground">{list.title}</p>
                    <p className="font-mono text-xs text-muted-foreground">
                      <span className="capitalize">{list.site}</span> · {list.kind}
                      {list.count !== null ? ` · ${list.count} models` : ""}
                    </p>
                  </div>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={already || follow.isPending}
                    onClick={() =>
                      follow.mutate({
                        site: list.site,
                        list_id: list.list_id,
                        kind: list.kind,
                        title: list.title,
                        mode: "review",
                      })
                    }
                  >
                    {already ? "Following" : "Follow"}
                  </Button>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
