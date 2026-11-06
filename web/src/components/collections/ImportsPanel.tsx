/**
 * Import history card: two tabs -- Downloads (everything that isn't failed)
 * and Failures (N undismissed). Rows are grouped under sticky date headers
 * (`lib/dateGroups.ts`), keyset-paged 25 at a time (`useImportsList`), and
 * auto-load the next page when the sentinel scrolls into view, with a "Show
 * more" button as fallback. Failures hides dismissed rows unless "Show
 * dismissed" is on. FAILED rows surface their stored `error`, Retry
 * (`POST /imports/{id}/retry`; a 409/404 is toasted by `queryClient.ts`'s
 * global `MutationCache.onError`) and Dismiss; Done rows link to their model.
 * Stays live via SSE invalidation plus a short poll while anything is
 * non-terminal -- see `api/imports.ts`.
 */
import {
  type ImportsView,
  useDismissFailedImports,
  useDismissImport,
  useFailedImportsCount,
  useImportsList,
  useRetryImport,
} from "@/api/imports";
import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import type { ImportOut, ImportState } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { dateGroupLabel, groupByDate } from "@/lib/dateGroups";
import { formatRelativeTime } from "@/lib/format";


/** How long "Dismiss all failed" waits (undo window) before it is sent. */
const UNDO_MS = 6000;

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
  const { t } = useTranslation();
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
              {t(`imports.state.${imp.state}`)}
            </Badge>
            {dismissed ? <Badge variant="outline">{t("imports.dismissed")}</Badge> : null}
            {imp.state === "done" && imp.model_slug ? (
              <Link
                to="/models/$slug"
                params={{ slug: imp.model_slug }}
                className="min-w-0 text-sm font-medium break-all text-foreground hover:underline"
                title={imp.url}
              >
                {title}
              </Link>
            ) : (
              <span className="min-w-0 text-sm font-medium break-all text-foreground" title={imp.url}>
                {title}
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {formatRelativeTime(imp.created_at)}
            {typeof imp.meta?.profiles_total === "number" &&
            typeof imp.meta?.profiles_kept === "number" &&
            imp.meta.profiles_kept < imp.meta.profiles_total
              ? ` · ${t("imports.filtered", { kept: imp.meta.profiles_kept, total: imp.meta.profiles_total })}`
              : ""}
          </p>
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
                {t("imports.dismiss")}
              </Button>
            ) : null}
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={retry.isPending}
              onClick={() => retry.mutate(imp.id)}
            >
              {retry.isPending ? t("imports.retrying") : t("imports.retry")}
            </Button>
          </div>
        ) : null}
      </div>
    </li>
  );
}

export function ImportsPanel() {
  const { t } = useTranslation();
  const [dismissAllPending, setDismissAllPending] = useState(false);
  const [view, setView] = useState<ImportsView>("downloads");
  const [showDismissed, setShowDismissed] = useState(false);
  const importsQuery = useImportsList(view, showDismissed);
  const imports = importsQuery.data ?? [];
  const groups = groupByDate(imports, (r) => r.created_at);
  const dismissAll = useDismissFailedImports();
  const undismissed = useFailedImportsCount();

  // No server-side undo exists, so the request is simply held back for a few
  // seconds; "Undo" in the toast cancels it before anything is sent.
  function dismissAllWithUndo() {
    const timer = window.setTimeout(() => {
      setDismissAllPending(false);
      dismissAll.mutate();
    }, UNDO_MS);
    setDismissAllPending(true);
    toast(t("imports.dismissAllToast", { count: undismissed }), {
      duration: UNDO_MS,
      action: {
        label: t("imports.undo"),
        onClick: () => {
          window.clearTimeout(timer);
          setDismissAllPending(false);
        },
      },
    });
  }

  const { hasNextPage, isFetchingNextPage, fetchNextPage } = importsQuery;
  const sentinel = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !hasNextPage || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting) && !isFetchingNextPage) void fetchNextPage();
    });
    io.observe(el);
    return () => io.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage, imports.length]);

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle>{t("imports.title")}</CardTitle>
        <Tabs value={view} onValueChange={(v) => setView(v as ImportsView)}>
          <TabsList>
            <TabsTrigger value="downloads">{t("imports.downloads")}</TabsTrigger>
            <TabsTrigger value="failures">
              {t("imports.failures")}
              {undismissed > 0 ? ` (${undismissed})` : ""}
            </TabsTrigger>
          </TabsList>
        </Tabs>
      </CardHeader>
      <CardContent>
        {view === "failures" ? (
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <Switch checked={showDismissed} onCheckedChange={setShowDismissed} aria-label={t("imports.showDismissed")} />
              {t("imports.showDismissed")}
            </label>
            {undismissed > 0 ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={dismissAll.isPending || dismissAllPending}
                onClick={dismissAllWithUndo}
              >
                {t("imports.dismissAll", { n: undismissed })}
              </Button>
            ) : null}
          </div>
        ) : null}
        {importsQuery.isLoading ? (
          <Skeleton className="h-24 w-full rounded-lg" />
        ) : imports.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {view === "failures"
              ? t("imports.noneFailed")
              : t("imports.none")}
          </p>
        ) : (
          <div data-testid="imports-list">
            {groups.map((g) => (
              <section key={g.label} aria-label={dateGroupLabel(g.label)} className="mb-3">
                <h3 className="sticky top-0 z-10 bg-card py-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                  {dateGroupLabel(g.label)}
                </h3>
                <ul className="space-y-2">
                  {g.rows.map((imp) => (
                    <ImportRow key={imp.id} imp={imp} />
                  ))}
                </ul>
              </section>
            ))}
            {hasNextPage ? (
              <>
                <div ref={sentinel} aria-hidden className="h-px" />
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={isFetchingNextPage}
                  onClick={() => void fetchNextPage()}
                >
                  {isFetchingNextPage ? t("imports.loading") : t("imports.showMore")}
                </Button>
              </>
            ) : null}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
