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
import { Fragment, useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { ArrowDownIcon, ArrowUpIcon, ChevronDownIcon, ChevronRightIcon, RotateCcwIcon, Trash2Icon, XIcon } from "lucide-react";

import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { PLATE_NOT_EMPTY } from "@/api/plateCheck";
import { usePrinters } from "@/api/printers";
import { useCancelQueueSlice, useMoveQueueEntry, useQueue, useRemoveQueueEntry, useRetryQueueEntry } from "@/api/queue";
import type { PrinterOut, QueueEntry, QueueState } from "@/api/types";
import { BatchesPanel } from "@/components/print/BatchesPanel";
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

const STATE_LABELS: Record<QueueState, string> = {
  waiting: "Waiting",
  slicing: "Slicing",
  ready: "Ready",
  dispatched: "Dispatched",
  held: "Held",
  failed: "Failed",
  done: "Done",
  skipped: "Skipped",
};

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
  skipped: "bg-transparent text-amber-600 border-amber-500 dark:text-amber-400",
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
  const parts: string[] = [];
  if (attempts != null) parts.push(`${attempts}/3 attempts`);
  if (lastError) parts.push(lastError);
  const title = parts.length > 0 ? parts.join(" — ") : undefined;

  if (state === "waiting" && lastError === PLATE_NOT_EMPTY) {
    return (
      <Badge variant="outline" className={STATE_CLASSES.held} title={title} aria-label="Held: plate not empty">
        Plate not empty
      </Badge>
    );
  }

  return (
    <Badge variant="outline" className={STATE_CLASSES[state]} title={title}>
      {STATE_LABELS[state]}
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
  if (!reason) return null;
  return (
    <Badge
      variant="outline"
      className={STATE_CLASSES.held}
      title={reason}
      aria-label={`Held: ${reason}`}
    >
      Held
    </Badge>
  );
}

// P7 review/billing plan Task 9: Pending review (slate outline), Rejected
// (destructive outline), Over budget (amber outline) -- next to the
// dispatch `QueueStateChip`, not replacing it (an entry can be e.g. both
// `held`/over-budget AND still `review_state=pending`).
function ReviewChips({ entry }: { entry: QueueEntry }) {
  const retryEntry = useRetryQueueEntry();
  const overBudget = entry.hold_reason === "over_budget";
  return (
    <>
      {entry.review_state === "pending" ? (
        <Badge variant="outline" className="border-slate-400 bg-transparent text-slate-500 dark:text-slate-400">
          Pending review
        </Badge>
      ) : null}
      {entry.review_state === "rejected" ? (
        <Badge variant="outline" className="border-destructive bg-transparent text-destructive" title={entry.review_note ?? undefined}>
          Rejected
        </Badge>
      ) : null}
      {overBudget ? (
        <span className="inline-flex items-center gap-1.5">
          <Badge variant="outline" className="border-amber-500 bg-transparent text-amber-600 dark:text-amber-400">
            Over budget
          </Badge>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={retryEntry.isPending}
            onClick={() => retryEntry.mutate(entry.id)}
          >
            Retry budget
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
  const search = useSearch({ strict: false }) as { tab?: "queue" | "batches"; batch?: number };
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
          <QueueLanes />
        </TabsContent>
        <TabsContent value="batches" className="space-y-4">
          <BatchesPanel openBatchId={search.batch ?? null} />
        </TabsContent>
      </Tabs>
    </PageContainer>
  );
}

function QueueLanes() {
  const [includeDone, setIncludeDone] = useState(false);
  const queueQuery = useQueue({ includeDone });
  const printers = usePrinters();
  const entries = queueQuery.data ?? [];
  const lanes = groupByLane(entries, printers.data ?? []);

  const columns: { key: number | typeof ANY_PRINTER; title: string }[] = [
    ...(printers.data ?? []).map((p) => ({ key: p.id, title: p.name })),
    { key: ANY_PRINTER, title: "Any printer" },
  ];

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold text-foreground">Print queue</h1>
          <p className="text-sm text-muted-foreground">
            One lane per printer, plus unassigned entries waiting for the dispatcher.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <Switch checked={includeDone} onCheckedChange={setIncludeDone} aria-label="Show finished" />
          Show finished
        </label>
      </div>

      {queueQuery.isLoading ? (
        <Skeleton className="h-64 w-full rounded-lg" />
      ) : queueQuery.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {queueQuery.error instanceof ApiError ? queueQuery.error.detail : "Couldn't load the queue."}
        </p>
      ) : (
        <div className="flex gap-4 overflow-x-auto pb-2">
          {columns.map((col) => (
            <QueueLane
              key={col.key}
              title={col.title}
              printerId={col.key === ANY_PRINTER ? null : col.key}
              entries={lanes.get(col.key) ?? []}
            />
          ))}
        </div>
      )}
    </>
  );
}

function QueueLane({ title, entries, printerId }: { title: string; entries: QueueEntry[]; printerId: number | null }) {
  return (
    <Card className="w-80 shrink-0">
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-sm">{title}</CardTitle>
          <LaneResumeButton printerId={printerId} entries={entries} />
        </div>
        <CardDescription>{entries.length} queued</CardDescription>
      </CardHeader>
      <CardContent>
        {entries.length === 0 ? (
          <p className="py-4 text-center text-sm text-muted-foreground">Nothing here.</p>
        ) : (
          <ul className="divide-y divide-border">
            {entries.map((entry, index) => (
              <Fragment key={entry.id}>
                <QueueRow entry={entry} prev={entries[index - 1]} next={entries[index + 1]} />
              </Fragment>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function QueueRow({
  entry,
  prev,
  next,
}: {
  entry: QueueEntry;
  prev: QueueEntry | undefined;
  next: QueueEntry | undefined;
}) {
  const moveEntry = useMoveQueueEntry();
  const removeEntry = useRemoveQueueEntry();
  const retryEntry = useRetryQueueEntry();
  const cancelSlice = useCancelQueueSlice();
  const [expanded, setExpanded] = useState(false);
  const { can } = useAuth();
  const model = entry.model;
  const state = entry.state ?? "waiting";
  const canRetry = state === "failed" || state === "held";

  const name = entry.file_name ?? model?.name ?? "—";

  return (
    <li className="py-2.5">
    <div className="flex items-center gap-2">
      <button
        type="button"
        aria-expanded={expanded}
        aria-label={`${expanded ? "Hide" : "Show"} details for ${name}`}
        className="shrink-0 text-muted-foreground hover:text-foreground"
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? <ChevronDownIcon className="size-4" /> : <ChevronRightIcon className="size-4" />}
      </button>
      <div className="flex size-9 shrink-0 items-center justify-center overflow-hidden rounded bg-muted">
        {model?.cover ? (
          <img src={model.cover} alt="" className="size-full object-cover" />
        ) : (
          <span className="text-[10px] font-semibold text-muted-foreground">
            {model?.formats[0] ? FORMAT_LABELS[model.formats[0]] : "—"}
          </span>
        )}
      </div>

      <div className="min-w-0 flex-1">
        {model ? (
          <Link to="/models/$slug" params={{ slug: model.slug }} className="truncate text-sm font-medium hover:underline">
            {name}
          </Link>
        ) : (
          <span className="truncate text-sm font-medium">{name}</span>
        )}
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          {entry.plate != null ? <span>Plate {entry.plate}</span> : null}
          <span>×{entry.copies ?? 1}</span>
          <span>{entry.recipe_name ?? "As sliced"}</span>
          <span>Added {formatDate(entry.added_at)}</span>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <QueueStateChip state={state} lastError={entry.last_error} attempts={entry.attempts ?? 0} />
          <HeldChip reason={entry.printer_hold_reason} />
          <ReviewChips entry={entry} />
          <ScheduleChips entry={entry} />
        </div>
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
            aria-label={`Cancel slicing ${name}`}
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
            aria-label={`Retry ${name}`}
            disabled={retryEntry.isPending}
            onClick={() => retryEntry.mutate(entry.id)}
          >
            <RotateCcwIcon className="size-4" />
          </Button>
        ) : null}
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={`Move ${name} up`}
          disabled={!prev || moveEntry.isPending}
          onClick={() => prev && moveEntry.mutate({ entryId: entry.id, position: prev.position })}
        >
          <ArrowUpIcon className="size-4" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={`Move ${name} down`}
          disabled={!next || moveEntry.isPending}
          onClick={() => next && moveEntry.mutate({ entryId: entry.id, position: next.position })}
        >
          <ArrowDownIcon className="size-4" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={`Remove ${name}`}
          disabled={removeEntry.isPending}
          onClick={() => removeEntry.mutate(entry.id)}
        >
          <Trash2Icon className="size-4" />
        </Button>
      </div>
    </div>
    {expanded ? <QueueEntryDetail entry={entry} /> : null}
    </li>
  );
}
