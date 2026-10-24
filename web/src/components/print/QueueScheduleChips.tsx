/**
 * T4 queue-row scheduling chips + inline schedule editor: scheduled (with
 * cancel-schedule), manual start (+ Start for `queue:review`), skipped
 * (+ Resume printer), preheating/soaking stage, power-off, batch link.
 */
import { Link } from "@tanstack/react-router";
import { CalendarClockIcon, Loader2Icon, PencilIcon, XIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useAuth } from "@/api/auth";
import { useCancelSchedule, usePatchQueueEntry, useResumePrinterQueue, useStartQueueEntry } from "@/api/queue";
import type { QueueEntry } from "@/api/types";
import { SchedulingFields } from "@/components/print/SchedulingSection";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { draftToPatch, isoToLocal, type ScheduleDraft } from "@/lib/schedule";

const EDITABLE = new Set(["waiting", "ready", "held", "slicing"]);

export function entryDraft(entry: QueueEntry): ScheduleDraft {
  return {
    startAt: isoToLocal(entry.scheduled_at),
    manualStart: !!entry.manual_start,
    requirePrev: !!entry.require_previous_success,
    autoOff: !!entry.auto_off_after,
    preheat: entry.preheat_override ?? "inherit",
    chamberTarget: entry.preheat_chamber_target != null ? String(entry.preheat_chamber_target) : "",
  };
}

function SchedulePopover({ entry }: { entry: QueueEntry }) {
  const { t } = useTranslation();
  const patch = usePatchQueueEntry();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<ScheduleDraft>(() => entryDraft(entry));
  return (
    <Popover
      open={open}
      onOpenChange={(o) => {
        if (o) setDraft(entryDraft(entry));
        setOpen(o);
      }}
    >
      <PopoverTrigger asChild>
        <Button type="button" variant="ghost" size="icon-sm" aria-label={t("queueSched.chip.edit")}>
          <PencilIcon className="size-3.5" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-96 space-y-3">
        <p className="text-sm font-medium">{t("queueSched.edit.title")}</p>
        <SchedulingFields draft={draft} onChange={setDraft} idPrefix={`sched-edit-${entry.id}`} />
        <div className="flex justify-end gap-2">
          <Button type="button" size="sm" variant="outline" onClick={() => setOpen(false)}>
            {t("queueSched.edit.cancel")}
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={patch.isPending}
            onClick={() =>
              patch.mutate({ entryId: entry.id, body: draftToPatch(draft) }, { onSuccess: () => setOpen(false) })
            }
          >
            {t("queueSched.edit.save")}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

export function ScheduleChips({ entry }: { entry: QueueEntry }) {
  const { t } = useTranslation();
  const { can } = useAuth();
  const start = useStartQueueEntry();
  const cancel = useCancelSchedule();
  const resume = useResumePrinterQueue();
  const canReview = can("queue:review");
  const printerId = entry.printer_id ?? entry.assigned_printer_id ?? null;
  const state = entry.state ?? "waiting";
  const waiting = EDITABLE.has(state);

  const when = entry.scheduled_at ? new Date(entry.scheduled_at).toLocaleString() : null;
  const awaitingRelease = !!entry.manual_start && !entry.released_at && waiting;
  const reason = entry.skipped_reason ?? entry.hold_reason ?? null;

  return (
    <>
      {when && waiting ? (
        <span className="inline-flex items-center gap-0.5">
          <Badge variant="outline" className="border-sky-500 bg-transparent text-sky-600 dark:text-sky-400">
            <CalendarClockIcon className="size-3" />
            {t("queueSched.chip.scheduled", { when })}
          </Badge>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={t("queueSched.chip.cancelSchedule")}
            title={t("queueSched.chip.cancelSchedule")}
            disabled={cancel.isPending}
            onClick={() => cancel.mutate(entry.id)}
          >
            <XIcon className="size-3.5" />
          </Button>
        </span>
      ) : null}
      {awaitingRelease ? (
        <span className="inline-flex items-center gap-1.5">
          <Badge variant="outline" className="border-amber-500 bg-transparent text-amber-600 dark:text-amber-400">
            {t("queueSched.chip.manual")}
          </Badge>
          {canReview ? (
            <Button type="button" size="sm" variant="outline" disabled={start.isPending} onClick={() => start.mutate(entry.id)}>
              {t("queueSched.chip.start")}
            </Button>
          ) : null}
        </span>
      ) : null}
      {state === "skipped" ? (
        <span className="inline-flex items-center gap-1.5">
          <Badge
            variant="outline"
            className="border-amber-500 bg-transparent text-amber-600 dark:text-amber-400"
            title={reason ?? undefined}
          >
            {reason
              ? t("queueSched.chip.skippedReason", {
                  reason: t(`queueSched.chip.${reason}`, { defaultValue: reason }),
                })
              : t("queueSched.chip.skipped")}
          </Badge>
          {canReview && printerId != null ? (
            <Button type="button" size="sm" variant="outline" disabled={resume.isPending} onClick={() => resume.mutate(printerId)}>
              {t("queueSched.chip.resume")}
            </Button>
          ) : null}
        </span>
      ) : null}
      {entry.stage ? (
        <Badge variant="outline" className="border-transparent bg-state-printing/15 text-state-printing">
          <Loader2Icon className="size-3 animate-spin" />
          {t(`queueSched.chip.${entry.stage}`)}
        </Badge>
      ) : null}
      {entry.auto_off_after ? (
        <Badge variant="outline" className="bg-transparent text-muted-foreground">
          {t("queueSched.chip.powerOff")}
        </Badge>
      ) : null}
      {entry.batch ? (
        <Link to="/print/queue" search={{ tab: "batches", batch: entry.batch.id }}>
          <Badge variant="outline" className="bg-transparent text-muted-foreground hover:bg-muted">
            {t("queueSched.chip.batch", { name: entry.batch.name })}
          </Badge>
        </Link>
      ) : null}
      {waiting ? <SchedulePopover entry={entry} /> : null}
    </>
  );
}

/** Lane-header "Resume" -- shown when any entry in the lane is skipped. */
export function LaneResumeButton({ printerId, entries }: { printerId: number | null; entries: QueueEntry[] }) {
  const { t } = useTranslation();
  const { can } = useAuth();
  const resume = useResumePrinterQueue();
  if (printerId == null || !can("queue:review") || !entries.some((e) => e.state === "skipped")) return null;
  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      disabled={resume.isPending}
      onClick={() => resume.mutate(printerId)}
    >
      {t("queueSched.chip.resumeLane")}
    </Button>
  );
}
