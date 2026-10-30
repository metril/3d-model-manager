/**
 * `/` print-first home (P3 Task 8, Q3): a status summary bar, filter/sort
 * toolbar (URL params, `printerDashSearch.ts`), bulk-select toolbar and a
 * grid of bambuddy-layout `PrinterCard`s plus a right rail (`UpNextList`,
 * `RecentRunsList`), all fed by one `usePrintersOverview` poll.
 * `redirectForIndex` (`routes.tsx`) already keeps anyone without a visible
 * printer off this route unless they're an admin who could add one, so a
 * zero-printer render here shows the setup card rather than an empty state.
 *
 * Card size (S/M/L/XL) lives in localStorage (`useCardSize`) and drives the
 * grid min-width (`auto-fill minmax`; default L for 2 printers or fewer). A live `CameraTile` is only mounted on L/XL cards
 * for `can_print` items (the token mint requires that permission; smaller
 * cards link to the detail page's camera instead of streaming N feeds).
 */
import { useNavigate, useSearch } from "@tanstack/react-router";
import { PrinterIcon } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { usePrintersOverview } from "@/api/printers";
import { useReviewCount } from "@/api/queue";
import { CameraTile } from "@/components/print/CameraTile";
import { HaAlertBadge } from "@/components/print/HaAlertBadge";
import { PlateStallBadge } from "@/components/print/PlateStallBadge";
import { EmptyState } from "@/components/ui/empty-state";
import { BulkPrinterToolbar } from "@/components/print/BulkPrinterToolbar";
import { CARD_SIZES, gridStyle, useCardSize } from "@/components/print/card/cardSize";
import { ManagedPrinterCard } from "@/components/print/ManagedPrinterCard";
import { RecentRunsList } from "@/components/print/RecentRunsList";
import { ReviewInboxCard } from "@/components/print/ReviewInboxCard";
import { StatusSummaryBar } from "@/components/print/StatusSummaryBar";
import { UpNextList } from "@/components/print/UpNextList";
import { PrinterSetupCard } from "@/components/settings/PrinterSetupCard";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PageContainer } from "@/components/ui/page-container";
import { usePrintTitle } from "@/hooks/useDocumentTitle";
import { filterItems, sortGroups, summarize } from "@/lib/printerDashboard";
import { cn } from "@/lib/utils";
import { DASH_SORTS, parsePrintDashSearch, type PrintDashSearch } from "@/pages/printerDashSearch";

export function PrintDashboardPage() {
  const { t } = useTranslation();
  const overview = usePrintersOverview();
  const navigate = useNavigate();
  const search = parsePrintDashSearch(useSearch({ strict: false }) as Record<string, unknown>);
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  function patchSearch(patch: Partial<PrintDashSearch>) {
    void navigate({
      to: ".",
      search: ((prev: Record<string, unknown>) => ({ ...prev, ...patch })) as never,
      replace: true,
    });
  }
  const auth = useAuth();
  const canReview = auth.can("queue:review");
  const reviewCount = useReviewCount({ enabled: canReview });
  const printers = useMemo(() => overview.data?.printers ?? [], [overview.data]);
  const [size, setSize] = useCardSize(printers.length > 0 && printers.length <= 2 ? "L" : "M");
  usePrintTitle(printers);
  const summary = useMemo(() => summarize(printers), [printers]);
  const groups = useMemo(() => sortGroups(filterItems(printers, search), search.sort), [printers, search]);
  const upNext = overview.data?.up_next ?? [];
  const recentRuns = overview.data?.recent_runs ?? [];
  // P7 Task 9: the review inbox card joins the rail only for a reviewer
  // with something actually pending -- empty-but-mounted would just be
  // dead chrome.
  const railExtras: ReactNode[] = [];
  if (canReview && (reviewCount.data?.count ?? 0) > 0) {
    railExtras.push(<ReviewInboxCard key="review-inbox" />);
  }

  if (overview.isError && !overview.data) {
    return (
      <PageContainer width="default">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Print</h1>
        </div>
        <div className="space-y-2 py-8 text-center">
          <p role="alert" className="text-sm text-destructive">
            {overview.error instanceof ApiError ? overview.error.detail : "Couldn't load the print overview."}
          </p>
          <Button type="button" variant="outline" onClick={() => void overview.refetch()}>
            Retry
          </Button>
        </div>
      </PageContainer>
    );
  }

  if (!overview.isLoading && printers.length === 0) {
    return (
      <PageContainer width="default">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Print</h1>
          <p className="text-sm text-muted-foreground">Add a printer to start printing from here.</p>
        </div>
        <PrinterSetupCard />
      </PageContainer>
    );
  }

  return (
    <PageContainer width="fluid">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">Print</h1>
        <p className="text-sm text-muted-foreground">A live overview of your printers.</p>
      </div>

      <div className="flex flex-col gap-6 xl:flex-row">
        <div className="flex min-w-0 flex-1 flex-col gap-4">
          <StatusSummaryBar summary={summary} active={search.status} onSelect={(status) => patchSearch({ status })} />

          <div className="flex flex-wrap items-center gap-2" data-slot="dash-toolbar">
            <Input
              type="search"
              className="h-8 w-48"
              placeholder="Search printers"
              aria-label="Search printers"
              defaultValue={search.q ?? ""}
              onChange={(e) => patchSearch({ q: e.target.value || undefined })}
            />
            <label className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={search.offline === "hide"}
                onChange={(e) => patchSearch({ offline: e.target.checked ? "hide" : undefined })}
              />
              Hide offline
            </label>
            <label className="flex items-center gap-1.5 text-sm">
              Sort
              <select
                aria-label="Sort printers"
                className="h-8 rounded-md border bg-card px-2 text-sm"
                value={search.sort ?? "name"}
                onChange={(e) => patchSearch({ sort: e.target.value === "name" ? undefined : (e.target.value as PrintDashSearch["sort"]) })}
              >
                {DASH_SORTS.map((s) => (
                  <option key={s} value={s}>
                    {s === "eta" ? "ETA" : s[0].toUpperCase() + s.slice(1)}
                  </option>
                ))}
              </select>
            </label>
            <Button
              type="button"
              size="sm"
              variant={selectMode ? "default" : "outline"}
              aria-pressed={selectMode}
              onClick={() => {
                setSelectMode((v) => !v);
                setSelected(new Set());
              }}
            >
              Select
            </Button>
            <div role="radiogroup" aria-label="Card size" className="ml-auto inline-flex overflow-hidden rounded-md border">
              {CARD_SIZES.map((s) => (
                <button
                  key={s}
                  type="button"
                  role="radio"
                  aria-checked={size === s}
                  className={cn("px-2.5 py-1 text-xs", size === s ? "bg-primary text-primary-foreground" : "hover:bg-muted")}
                  onClick={() => setSize(s)}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>

          {selectMode && selected.size > 0 ? (
            <BulkPrinterToolbar
              selected={printers.filter((p) => selected.has(p.printer.id))}
              onDone={() => {
                setSelected(new Set());
                void overview.refetch();
              }}
              onClear={() => setSelected(new Set())}
            />
          ) : null}

          {groups.every((g) => g.items.length === 0) ? (
            <EmptyState
              icon={PrinterIcon}
              title={t("dash.empty.filters")}
              action={
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => patchSearch({ status: undefined, offline: undefined, q: undefined })}
                >
                  {t("dash.empty.clearFilters")}
                </Button>
              }
            />
          ) : null}
          {groups.map((group) => (
            <section key={group.key} className="flex flex-col gap-2">
              {group.label ? (
                <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {group.label} <span className="font-mono">({group.items.length})</span>
                </h2>
              ) : null}
              <div className={cn("grid gap-4", size === "L" || size === "XL" ? "gap-6" : "")} style={gridStyle(size)}>
                {group.items.map((item) => (
                  <ManagedPrinterCard
                    key={item.printer.id}
                    item={item}
                    size={size}
                    selected={selectMode ? selected.has(item.printer.id) : undefined}
                    onSelectedChange={
                      selectMode
                        ? (on) =>
                            setSelected((prev) => {
                              const next = new Set(prev);
                              if (on) next.add(item.printer.id);
                              else next.delete(item.printer.id);
                              return next;
                            })
                        : undefined
                    }
                    camera={
                      item.can_print && (size === "L" || size === "XL") ? (
                        <CameraTile printerId={item.printer.id} online={item.status.online} />
                      ) : undefined
                    }
                    badge={
                      item.ha_alert || item.plate_check_stalled ? (
                        <>
                          {item.ha_alert ? <HaAlertBadge reason={item.ha_alert} held={item.ha_held ?? false} /> : null}
                          {item.plate_check_stalled ? <PlateStallBadge /> : null}
                        </>
                      ) : undefined
                    }
                  />
                ))}
              </div>
            </section>
          ))}
        </div>

        <div className="flex flex-col gap-6 xl:w-80 xl:shrink-0">
          <UpNextList entries={upNext} />
          <RecentRunsList runs={recentRuns} />
          {railExtras}
        </div>
      </div>
    </PageContainer>
  );
}
