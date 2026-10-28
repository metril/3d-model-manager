/**
 * Recent imports card (import-health task T3): the last `GET /imports?limit=50`
 * rows, newest first, visually capped at `VISIBLE_LIMIT` with a muted "and N
 * more…" line -- a monitoring strip, not a paginated list. Every FAILED row
 * surfaces its stored `error` and offers a one-click Retry
 * (`POST /imports/{id}/retry`); a 409 (already retried, no longer failed) or
 * 404 (unknown id) is toasted by `queryClient.ts`'s global
 * `MutationCache.onError`, so there's no local error text beyond the row's
 * own `error` field. Stays live via `useImportsList()` (SSE invalidation +
 * a short poll while anything is non-terminal -- see `api/imports.ts`), so
 * no manual refresh is needed on this page.
 */
import {
  type ImportsFilter,
  useDismissFailedImports,
  useDismissImport,
  useFailedImportsCount,
  useImportsList,
  useRetryImport,
} from "@/api/imports";
import { useState } from "react";

import type { ImportOut, ImportState } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatRelativeTime } from "@/lib/format";


const STATE_LABEL: Record<ImportState, string> = {
  pending: "Pending",
  fetching: "Fetching",
  downloading: "Downloading",
  done: "Done",
  failed: "Failed",
};

// `failed` reuses the shared `destructive` Badge variant; `done` layers
// emerald and the in-flight states amber onto `outline`, since neither
// exists as a Badge variant of its own -- mirrors `PrintsTab.tsx`'s
// `RESULT_BADGE` (same "layer a status color onto outline" idiom).
const STATE_BADGE: Record<ImportState, { variant: "destructive" | "outline"; className?: string }> = {
  pending: { variant: "outline", className: "bg-warning/15 text-warning" },
  fetching: { variant: "outline", className: "bg-warning/15 text-warning" },
  downloading: { variant: "outline", className: "bg-warning/15 text-warning" },
  done: { variant: "outline", className: "bg-success/15 text-success" },
  failed: { variant: "destructive" },
};

/** The model's title if the import's `meta` happens to carry one, else the
 * source URL -- today's importer only ever stores `{cover_url, license,
 * files}` in `meta` (backend/app/tasks/importing.py), so this falls back to
 * the URL in practice, but stays forward-compatible with a future backend
 * that adds one. */
function importTitle(imp: ImportOut): string {
  const metaTitle = imp.meta?.title;
  return typeof metaTitle === "string" && metaTitle.trim() ? metaTitle : imp.url;
}

function ImportRow({ imp }: { imp: ImportOut }) {
  const retry = useRetryImport();
  const dismiss = useDismissImport();
  const dismissed = imp.state === "failed" && !!imp.dismissed_at;
  const badge = STATE_BADGE[imp.state];
  const title = importTitle(imp);

  return (
    <li className={`rounded-lg border border-border p-3${dismissed ? " opacity-60" : ""}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={badge.variant} className={badge.className}>
              {STATE_LABEL[imp.state]}
            </Badge>
            {dismissed ? <Badge variant="outline">Dismissed</Badge> : null}
            {/* `done` rows with a `model_id` would ideally link straight to the
                model, but the only route that shows one (`/models/$slug`) needs
                a slug the import list doesn't carry and no by-id route exists
                to redirect through -- left as plain text (see T3 report). */}
            <span className="min-w-0 text-sm font-medium break-all text-foreground" title={imp.url}>
              {title}
            </span>
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">{formatRelativeTime(imp.created_at)}</p>
          {imp.state === "failed" && imp.error ? (
            <p className="mt-1 max-w-prose text-xs whitespace-pre-wrap text-destructive/90">{imp.error}</p>
          ) : null}
        </div>
        {imp.state === "failed" ? (
          <div className="flex gap-2">
            {!dismissed ? (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={dismiss.isPending}
                onClick={() => dismiss.mutate(imp.id)}
              >
                Dismiss
              </Button>
            ) : null}
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={retry.isPending}
              onClick={() => retry.mutate(imp.id)}
            >
              {retry.isPending ? "Retrying…" : "Retry"}
            </Button>
          </div>
        ) : null}
      </div>
    </li>
  );
}

export function ImportsPanel() {
  const [filter, setFilter] = useState<ImportsFilter>("all");
  const importsQuery = useImportsList(filter);
  const imports = importsQuery.data ?? [];
  const dismissAll = useDismissFailedImports();
  const undismissed = useFailedImportsCount();

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>Recent imports</CardTitle>
        <div className="flex gap-1" role="group" aria-label="Filter imports">
          {(["all", "failed"] as const).map((f) => (
            <Button
              key={f}
              type="button"
              size="sm"
              variant={filter === f ? "secondary" : "ghost"}
              aria-pressed={filter === f}
              onClick={() => setFilter(f)}
            >
              {f === "all" ? "All" : "Failed"}
            </Button>
          ))}
        </div>
        {undismissed > 0 ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={dismissAll.isPending}
            onClick={() => dismissAll.mutate()}
          >
            Dismiss all failed ({undismissed})
          </Button>
        ) : null}
      </CardHeader>
      <CardContent>
        {importsQuery.isLoading ? (
          <Skeleton className="h-24 w-full rounded-lg" />
        ) : imports.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {filter === "failed"
              ? "No failed imports."
              : "No imports yet. Save a model from the extension or approve one from a collection review."}
          </p>
        ) : (
          <>
            <ul className="space-y-2" data-testid="imports-list">
              {imports.map((imp) => (
                <ImportRow key={imp.id} imp={imp} />
              ))}
            </ul>
            {importsQuery.hasNextPage ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="mt-2"
                disabled={importsQuery.isFetchingNextPage}
                onClick={() => void importsQuery.fetchNextPage()}
              >
                {importsQuery.isFetchingNextPage ? "Loading…" : "Show more"}
              </Button>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
