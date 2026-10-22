/** Archived run detail: artifacts, per-tray estimate, costs, and owner/admin grading. */
import { useState } from "react";

import { archiveFileUrl, archiveSnapshotUrl, archiveThumbnailUrl, useArchive, usePatchRun } from "@/api/archives";
import { useAuth } from "@/api/auth";
import type { ArchiveOut, PrintEntry } from "@/api/types";
import { PrintDialog } from "@/components/print/PrintDialog";
import { TimelapsePlayer } from "@/components/print/TimelapsePlayer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FilamentChip } from "@/components/ui/filament-chip";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { formatCost, formatDateTime, humanizeDuration } from "@/lib/format";

const FETCH_NOTE: Record<string, string> = {
  pending: "Fetching files from the printer...",
  missing: "The printer no longer has this run's files.",
  error: "Fetching files from the printer failed.",
};

function CostRow({ label, value, strong }: { label: string; value: number | null | undefined; strong?: boolean }) {
  return (
    <div className={`flex justify-between ${strong ? "font-medium" : ""}`}>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-mono tabular-nums">{formatCost(value)}</dd>
    </div>
  );
}

function Grading({ run }: { run: PrintEntry }) {
  const patch = usePatchRun();
  const [rejecting, setRejecting] = useState(run.quality === "reject");
  const [reason, setReason] = useState(run.failure_reason ?? "");
  const [grams, setGrams] = useState(run.filament_g != null ? String(run.filament_g) : "");
  const gramsValue = grams.trim() === "" ? NaN : Number(grams);

  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant={run.quality === "good" ? "default" : "outline"}
          onClick={() => {
            setRejecting(false);
            patch.mutate({ id: run.id, patch: { quality: "good" } }, {});
          }}
        >
          Good
        </Button>
        <Button size="sm" variant={rejecting ? "destructive" : "outline"} onClick={() => setRejecting(true)}>
          Reject
        </Button>
      </div>
      {rejecting ? (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="run-failure-reason">Failure reason</Label>
          <Input
            id="run-failure-reason"
            value={reason}
            maxLength={500}
            onChange={(e) => setReason(e.target.value)}
          />
          <Button
            size="sm"
            className="self-start"
            onClick={() =>
              patch.mutate(
                { id: run.id, patch: { quality: "reject", ...(reason.trim() ? { failure_reason: reason.trim() } : {}) } },
                {},
              )
            }
          >
            Save
          </Button>
        </div>
      ) : null}
      <div className="flex items-end gap-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="run-actual-grams">Actual grams</Label>
          <Input
            id="run-actual-grams"
            type="number"
            min={0}
            step="0.1"
            value={grams}
            onChange={(e) => setGrams(e.target.value)}
            className="w-32 font-mono tabular-nums"
          />
        </div>
        <Button
          size="sm"
          variant="outline"
          disabled={!Number.isFinite(gramsValue) || gramsValue < 0}
          onClick={() => patch.mutate({ id: run.id, patch: { filament_g: gramsValue } }, {})}
        >
          Save grams
        </Button>
      </div>
    </section>
  );
}

function Body({ archive }: { archive: ArchiveOut }) {
  const { data: me, can } = useAuth();
  const [reprintOpen, setReprintOpen] = useState(false);
  const run = archive.run;
  const canGrade =
    run != null &&
    can("library:write") &&
    (can("admin") || (me != null && archive.created_by_user_id != null && me.id === archive.created_by_user_id) ||
      (me != null && run.created_by_user_id === me.id));
  const note = FETCH_NOTE[archive.fetch_state];

  return (
    <div className="space-y-5 overflow-y-auto px-4 pb-6 text-sm">
      {archive.has_thumbnail ? (
        <img src={archiveThumbnailUrl(archive.id)} alt="" className="max-h-48 rounded-lg border border-border" />
      ) : null}
      {archive.has_timelapse ? (
        <TimelapsePlayer
          archiveId={archive.id}
          poster={archive.has_snapshot ? archiveSnapshotUrl(archive.id) : undefined}
          className="aspect-video"
        />
      ) : archive.has_snapshot ? (
        <img
          src={archiveSnapshotUrl(archive.id)}
          alt="Captured snapshot"
          className="aspect-video w-full rounded-[6px] border border-border object-cover"
        />
      ) : null}
      {archive.media_note ? <p className="text-muted-foreground">{archive.media_note}</p> : null}
      <div className="flex flex-wrap items-center gap-2 font-mono text-xs text-muted-foreground">
        <Badge variant={archive.result === "completed" ? "default" : "destructive"}>{archive.result}</Badge>
        <span>{archive.printer_name}</span>
        {archive.duration_s != null ? <span className="tabular-nums">{humanizeDuration(archive.duration_s)}</span> : null}
        <span>{formatDateTime(archive.finished_at ?? archive.created_at)}</span>
      </div>
      {note ? <p className="text-muted-foreground">{note}</p> : null}
      {archive.fetch_error ? <p className="text-destructive">{archive.fetch_error}</p> : null}
      {archive.est_filament && archive.est_filament.length > 0 ? (
        <ul className="space-y-1">
          {archive.est_filament.map((f, i) => (
            <li key={i} className="flex items-center gap-2">
              <FilamentChip color={f.color ?? undefined} material={f.type ?? "Filament"} size="sm" />
              {f.grams != null ? <span className="font-mono tabular-nums">{f.grams} g</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
      {run ? (
        <dl className="space-y-1">
          {run.energy_kwh != null ? (
            <div className="flex justify-between">
              <dt className="text-muted-foreground">Energy used</dt>
              <dd className="font-mono tabular-nums">{run.energy_kwh.toFixed(2)} kWh</dd>
            </div>
          ) : null}
          <CostRow label="Filament" value={run.filament_cost} />
          <CostRow label="Machine" value={run.machine_cost} />
          <CostRow label="Energy" value={run.energy_cost} />
          <CostRow label="Total" value={run.total_cost} strong />
        </dl>
      ) : null}
      {run?.quality === "reject" && run.failure_reason && !canGrade ? (
        <p className="text-muted-foreground">Rejected: {run.failure_reason}</p>
      ) : null}
      {canGrade && run ? <Grading key={run.id} run={run} /> : null}
      {can("printers:print") ? (
        <>
          <Button type="button" variant="outline" size="sm" onClick={() => setReprintOpen(true)}>
            Reprint
          </Button>
          <PrintDialog
            open={reprintOpen}
            onOpenChange={setReprintOpen}
            source={{ kind: "archive", archiveId: archive.id }}
          />
        </>
      ) : null}
      {archive.has_file ? (
        <Button asChild variant="outline" size="sm">
          <a href={archiveFileUrl(archive.id)} download>
            Download 3MF
          </a>
        </Button>
      ) : null}
    </div>
  );
}

export function RunDetailSheet({
  archiveId,
  onOpenChange,
}: {
  archiveId: number | null;
  onOpenChange: (open: boolean) => void;
}) {
  const { data } = useArchive(archiveId);
  return (
    <Sheet open={archiveId != null} onOpenChange={onOpenChange}>
      <SheetContent className="w-full sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{data ? (data.model_name ?? data.subtask_name ?? `Run #${data.id}`) : "Run"}</SheetTitle>
          <SheetDescription>Archived print run</SheetDescription>
        </SheetHeader>
        {data ? <Body archive={data} /> : null}
      </SheetContent>
    </Sheet>
  );
}
