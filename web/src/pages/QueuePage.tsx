/**
 * Print queue page (Branch 4 Task 2; rebuilt by the P6 "slicer, profiles
 * and dispatch" plan, Task 10, as per-printer lanes): one column per
 * visible printer, plus an "Any printer" lane for entries with no
 * `printer_id` (unassigned -- the dispatcher's `select_printers` will pick
 * one once they're `ready`). A DISPATCHED entry moves into whichever
 * printer actually claimed it (`assigned_printer_id`), which may differ
 * from the originally requested `printer_id` under `spread`/`first_n`
 * fanout -- mirrors `app.services.printer_overview`'s own
 * `printer_id ?? assigned_printer_id` grouping rule.
 *
 * Each row shows a thumb, the file/model name, plate, copies, the
 * recipe's name (or "As sliced" for a bare/no-recipe entry), a
 * `QueueStateChip` (hover/focus reveals `last_error` + `attempts/3`), and
 * Retry (only `failed`/`held`) / Remove. Reordering still PATCHes the
 * entry to an absolute global `position` -- unchanged from the flat-list
 * page, just now computed against the entry's neighbor WITHIN its own
 * lane rather than the whole queue.
 */
import { Fragment, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { ArrowDownIcon, ArrowUpIcon, ChevronDownIcon, ListOrderedIcon, ChevronRightIcon, RotateCcwIcon, Trash2Icon, XIcon } from "lucide-react";

import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { PLATE_NOT_EMPTY } from "@/api/plateCheck";
import { usePrinters } from "@/api/printers";
import { useCancelQueueSlice, useEnqueue, useMoveQueueEntry, useQueue, useRemoveQueueEntry, useRetryQueueEntry } from "@/api/queue";
import type { PrinterOut, QueueEnqueueBody, QueueEntry, QueueState } from "@/api/types";
import { OverflowMenu } from "@/components/ui/overflow-menu";
import { EmptyState } from "@/components/ui/empty-state";
import { BatchesPanel } from "@/components/print/BatchesPanel";
import { HoverPreview, QueueFilamentChips } from "@/components/print/QueueFilaments";
import { QueueEntryDetail } from "@/components/print/QueueEntryDetail";
import { LaneResumeButton, ScheduleChips } from "@/components/print/QueueScheduleChips";
import { ReviewActions } from "@/components/print/ReviewActions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageContainer } from "@/components/ui/page-container";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatDate } from "@/lib/format";
import { FORMAT_LABELS } from "@/lib/formatMeta";

const ANY_PRINTER = "any" as const;

const STATE_KEYS: readonly QueueState[] = ["waiting", "slicing", "ready", "dispatched", "held", "failed", "done", "skipped"];

// P3's state-tone vocabulary (`index.css`'s `--state-*`), reused here
// rather than introducing a second color system: idle-muted
// (waiting/slicing), slate (ready), amber (dispatched, the one "active"
// state), slate outline (held), destructive (failed), finished green
// (done).
const STATE_CLASSES: Record<QueueState, string> = {
  waiting: "bg-state-idle/10 text-state-idle border-transparent",
  slicing: "bg-state-idle/10 text-state-idle border-transparent",
  ready: "bg-state-paused/15 text-state-paused border-transparent",
  dispatched: "bg-state-printing/15 text-state-printing border-transparent",
  held: "bg-transparent text-state-paused border-state-paused",
  failed: "bg-destructive/10 text-destructive border-transparent",
  done: "bg-state-finished/15 text-state-finished border-transparent",
  skipped: "bg-transparent text-warning border-warning",
};

export function QueueStateChip({
  state,
  lastError,
  attempts,
}: {
  state: QueueState;
  lastError?: string | null;
  attempts?: number;
}) {
  const { t } = useTranslation();
  const parts: string[] = [];
  if (attempts != null) parts.push(t("queuePage.attempts", { n: attempts }));
  if (lastError) parts.push(lastError);
  const title = parts.length > 0 ? parts.join(" — ") : undefined;

  if (state === "waiting" && lastError === PLATE_NOT_EMPTY) {
    return (
      <Badge variant="outline" className={STATE_CLASSES.held} title={title} aria-label={t("queuePage.heldPlate")}>
        {t("queuePage.plateNotEmpty")}
      </Badge>
    );
  }

  return (
    <Badge variant="outline" className={STATE_CLASSES[state]} title={title}>
      {STATE_KEYS.includes(state) ? t(`queuePage.state.${state}`) : state}
    </Badge>
  );
}

// P8 Task 9: a firing HA alert link (`hold_queue=true`) is holding the
// dispatcher off this entry's printer target -- separate from `hold_reason`
// (the review/billing hold `ReviewChips` renders below), so both can show
// on the same row. Same `held` tone as `QueueStateChip`'s own `held` state
// (slate outline) -- the label stays plain "Held"; the reason only shows
// as the hover/focus tooltip and aria-label.
function HeldChip({ reason }: { reason?: string | null }) {
  const { t } = useTranslation();
  if (!reason) return null;
  return (
    <Badge
      variant="outline"
      className={STATE_CLASSES.held}
      title={reason}
      aria-label={t("queuePage.heldReason", { reason })}
    >
      {t("queuePage.state.held")}
    </Badge>
  );
}

// P7 review/billing plan Task 9: Pending review (slate outline), Rejected
// (destructive outline), Over budget (amber outline) -- next to the
// dispatch `QueueStateChip`, not replacing it (an entry can be e.g. both
// `held`/over-budget AND still `review_state=pending`).
function ReviewChips({ entry }: { entry: QueueEntry }) {
  const { t } = useTranslation();
  const retryEntry = useRetryQueueEntry();
  const overBudget = entry.hold_reason === "over_budget";
  return (
    <>
      {entry.review_state === "pending" ? (
        <Badge variant="outline" className="border-muted-foreground bg-transparent text-muted-foreground">
          {t("queuePage.pendingReview")}
        </Badge>
      ) : null}
      {entry.review_state === "rejected" ? (
        <Badge variant="outline" className="border-destructive bg-transparent text-destructive" title={entry.review_note ?? undefined}>
          {t("queuePage.rejected")}
        </Badge>
      ) : null}
      {overBudget ? (
        <span className="inline-flex items-center gap-1.5">
          <Badge variant="outline" className="border-warning bg-transparent text-warning">
            {t("queuePage.overBudget")}
          </Badge>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={retryEntry.isPending}
            onClick={() => retryEntry.mutate(entry.id)}
          >
            {t("queuePage.retryBudget")}
          </Button>
        </span>
      ) : null}
    </>
  );
}

function laneKey(entry: QueueEntry): number | typeof ANY_PRINTER {
  const target = entry.printer_id ?? entry.assigned_printer_id ?? null;
  return target ?? ANY_PRINTER;
}

function groupByLane(entries: QueueEntry[], printers: PrinterOut[]) {
  const lanes = new Map<number | typeof ANY_PRINTER, QueueEntry[]>();
  lanes.set(ANY_PRINTER, []);
  for (const p of printers) lanes.set(p.id, []);
  for (const entry of entries) {
    const key = laneKey(entry);
    if (!lanes.has(key)) lanes.set(key, []);
    lanes.get(key)!.push(entry);
  }
  return lanes;
}

export function QueuePage() {
  const { t } = useTranslation();
  const search = useSearch({ strict: false }) as { tab?: "queue" | "batches"; batch?: number; entry?: number };
  const navigate = useNavigate();
  const { can } = useAuth();
  const canReview = can("queue:review");
  const tab = canReview && search.tab === "batches" ? "batches" : "queue";
  return (
    <PageContainer width="fluid">
      <Tabs
        value={tab}
        onValueChange={(v) => void navigate({ to: "/print/queue", search: { tab: v as "queue" | "batches" } })}
      >
        {canReview ? (
          <TabsList>
            <TabsTrigger value="queue">{t("queueSched.tabs.queue")}</TabsTrigger>
            <TabsTrigger value="batches">{t("queueSched.tabs.batches")}</TabsTrigger>
          </TabsList>
        ) : null}
        <TabsContent value="queue" className="space-y-4">
          <QueueLanes highlightId={search.entry ?? null} />
        </TabsContent>
        <TabsContent value="batches" className="space-y-4">
          <BatchesPanel openBatchId={search.batch ?? null} />
        </TabsContent>
      </Tabs>
    </PageContainer>
  );
}

function QueueLanes({ highlightId }: { highlightId: number | null }) {
  const { t } = useTranslation();
  // A deep-linked entry may already be finished; show finished entries so it can be found.
  const [includeDone, setIncludeDone] = useState(highlightId != null);
  const queueQuery = useQueue({ includeDone });
  const printers = usePrinters();
  const entries = queueQuery.data ?? [];
  const lanes = groupByLane(entries, printers.data ?? []);
  useEffect(() => {
    if (highlightId != null) setIncludeDone(true);
  }, [highlightId]);
  // Scroll to the deep-linked row once per highlightId (not on every refetch/remount).
  const scrolledFor = useRef<number | null>(null);
  const found = highlightId != null && entries.some((e) => e.id === highlightId);
  useEffect(() => {
    if (!found || scrolledFor.current === highlightId) return;
    scrolledFor.current = highlightId;
    document.getElementById(`queue-entry-${highlightId}`)?.scrollIntoView?.({ block: "center", behavior: "smooth" });
  }, [found, highlightId]);

  const columns: { key: number | typeof ANY_PRINTER; title: string }[] = [
    ...(printers.data ?? []).map((p) => ({ key: p.id, title: p.name })),
    { key: ANY_PRINTER, title: t("queuePage.anyPrinter") },
  ];

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">{t("queuePage.title")}</h1>
          <p className="text-sm text-muted-foreground">
            {t("queuePage.subtitle")}
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <Switch checked={includeDone} onCheckedChange={setIncludeDone} aria-label={t("queuePage.showFinished")} />
          {t("queuePage.showFinished")}
        </label>
      </div>

      {highlightId != null && queueQuery.isSuccess && !found ? (
        <p role="status" className="text-sm text-muted-foreground">
          {t("queuePage.entryNotFound", { id: highlightId })}
        </p>
      ) : null}

      {queueQuery.isLoading ? (
        <Skeleton className="h-64 w-full rounded-lg" />
      ) : queueQuery.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {queueQuery.error instanceof ApiError ? queueQuery.error.detail : t("queuePage.loadError")}
        </p>
      ) : (
        <div className="flex gap-4 overflow-x-auto pb-2">
          {columns.map((col) => (
            <QueueLane
              key={col.key}
              title={col.title}
              printerId={col.key === ANY_PRINTER ? null : col.key}
              entries={lanes.get(col.key) ?? []}
              highlightId={highlightId}
            />
          ))}
        </div>
      )}
    </>
  );
}

function QueueLane({
  title,
  entries,
  printerId,
  highlightId,
}: {
  title: string;
  entries: QueueEntry[];
  printerId: number | null;
  highlightId: number | null;
}) {
  const { t } = useTranslation();
  return (
    <Card className="w-96 shrink-0">
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-sm">{title}</CardTitle>
          <LaneResumeButton printerId={printerId} entries={entries} />
        </div>
        <CardDescription>{t("queuePage.queued", { n: entries.length })}</CardDescription>
      </CardHeader>
      <CardContent>
        {entries.length === 0 ? (
          <EmptyState icon={ListOrderedIcon} title={t("dash.empty.queue")} />
        ) : (
          <ul className="divide-y divide-border">
            {entries.map((entry, index) => (
              <Fragment key={entry.id}>
                <QueueRow entry={entry} prev={entries[index - 1]} next={entries[index + 1]} highlighted={entry.id === highlightId} />
              </Fragment>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

/** What `POST /queue` needs to recreate a removed entry. Position is not
 * restorable (the backend appends), and `ams_mapping`/`options` are not on the
 * entry payload, so those fall back to defaults. */
function entryToEnqueueBody(entry: QueueEntry): QueueEnqueueBody {
  return {
    model_id: entry.model_id,
    file_id: entry.file_id ?? null,
    plate: entry.plate ?? null,
    printer_id: entry.printer_id ?? null,
    recipe_id: entry.recipe_id ?? null,
    copies: entry.copies,
    cost_center_id: entry.cost_center_id ?? null,
    scheduled_at: entry.scheduled_at ?? null,
    manual_start: entry.manual_start,
    require_previous_success: entry.require_previous_success,
    auto_off_after: entry.auto_off_after,
    preheat_override: entry.preheat_override ?? null,
    preheat_chamber_target: entry.preheat_chamber_target ?? null,
    batch_id: entry.batch_id ?? null,
  };
}

function QueueRow({
  entry,
  prev,
  next,
  highlighted = false,
}: {
  entry: QueueEntry;
  prev: QueueEntry | undefined;
  next: QueueEntry | undefined;
  highlighted?: boolean;
}) {
  const { t } = useTranslation();
  const moveEntry = useMoveQueueEntry();
  const removeEntry = useRemoveQueueEntry();
  const reEnqueue = useEnqueue();
  const retryEntry = useRetryQueueEntry();
  const cancelSlice = useCancelQueueSlice();
  const [expanded, setExpanded] = useState(false);
  const { can } = useAuth();
  const model = entry.model;
  const state = entry.state ?? "waiting";
  const canRetry = state === "failed" || state === "held";

  const name = entry.file_name ?? model?.name ?? "—";

  return (
    <li
      id={`queue-entry-${entry.id}`}
      data-highlighted={highlighted || undefined}
      className={highlighted ? "-mx-2 rounded-md bg-primary/10 px-2 py-2.5 ring-2 ring-primary" : "py-2.5"}
    >
    <div className="flex items-center gap-2">
      <button
        type="button"
        aria-expanded={expanded}
        aria-label={expanded ? t("queuePage.hideDetails", { name }) : t("queuePage.showDetails", { name })}
        className="shrink-0 text-muted-foreground hover:text-foreground"
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? <ChevronDownIcon className="size-4" /> : <ChevronRightIcon className="size-4" />}
      </button>
      <HoverPreview src={model?.cover}>
        <div className="flex size-9 shrink-0 items-center justify-center overflow-hidden rounded bg-muted">
          {model?.cover ? (
            <img src={model.cover} alt="" className="size-full object-cover" />
          ) : (
            <span className="text-[10px] font-semibold text-muted-foreground">
              {model?.formats[0] ? FORMAT_LABELS[model.formats[0]] : "—"}
            </span>
          )}
        </div>
      </HoverPreview>

      <div className="min-w-0 flex-1">
        {model ? (
          <Link to="/models/$slug" params={{ slug: model.slug }} title={name} className="line-clamp-2 break-words text-sm font-medium hover:underline">
            {name}
          </Link>
        ) : (
          <span title={name} className="line-clamp-2 break-words text-sm font-medium">{name}</span>
        )}
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          {entry.plate != null ? <span>{t("queuePage.plate", { plate: entry.plate })}</span> : null}
          <span>×{entry.copies ?? 1}</span>
          <span>{entry.recipe_name ?? t("queuePage.asSliced")}</span>
          <span>{t("queuePage.added", { date: formatDate(entry.added_at) })}</span>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <QueueStateChip state={state} lastError={entry.last_error} attempts={entry.attempts ?? 0} />
          <HeldChip reason={entry.printer_hold_reason} />
          <ReviewChips entry={entry} />
          <ScheduleChips entry={entry} />
        </div>
        <QueueFilamentChips entry={entry} />
        {entry.review_state === "pending" && can("queue:review") ? (
          <div className="mt-1.5">
            <ReviewActions entryId={entry.id} />
          </div>
        ) : null}
      </div>

      <div className="flex shrink-0 items-center gap-0.5">
        {state === "slicing" ? (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={t("queuePage.cancelSlicing", { name })}
            disabled={cancelSlice.isPending}
            onClick={() => cancelSlice.mutate(entry.id)}
          >
            <XIcon className="size-4" />
          </Button>
        ) : null}
        {canRetry ? (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={t("queuePage.retryNamed", { name })}
            disabled={retryEntry.isPending}
            onClick={() => retryEntry.mutate(entry.id)}
          >
            <RotateCcwIcon className="size-4" />
          </Button>
        ) : null}
        <OverflowMenu
          groups={[
            {
              items: [
                {
                  id: "up",
                  label: t("queuePage.moveUpShort"),
                  icon: ArrowUpIcon,
                  disabled: !prev || moveEntry.isPending,
                  onSelect: () => prev && moveEntry.mutate({ entryId: entry.id, position: prev.position }),
                },
                {
                  id: "down",
                  label: t("queuePage.moveDownShort"),
                  icon: ArrowDownIcon,
                  disabled: !next || moveEntry.isPending,
                  onSelect: () => next && moveEntry.mutate({ entryId: entry.id, position: next.position }),
                },
              ],
            },
            {
              items: [
                {
                  id: "remove",
                  label: t("queuePage.removeShort"),
                  icon: Trash2Icon,
                  destructive: true,
                  disabled: removeEntry.isPending,
                  onSelect: () =>
                    removeEntry.mutate(entry.id, {
                      onSuccess: () =>
                        toast(t("queuePage.removed", { name }), {
                          description: t("queuePage.undoHint"),
                          action: { label: t("queuePage.undo"), onClick: () => reEnqueue.mutate(entryToEnqueueBody(entry)) },
                        }),
                    }),
                },
              ],
            },
          ]}
        />
      </div>
    </div>
    {expanded ? <QueueEntryDetail entry={entry} /> : null}
    </li>
  );
}
