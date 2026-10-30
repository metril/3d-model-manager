/** Trim / speed-change an archived run's timelapse (server-side ffmpeg job). */
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  archiveSnapshotUrl,
  useProcessTimelapse,
  useTimelapseInfo,
  useTimelapseJob,
  useTimelapseThumbnails,
} from "@/api/archives";
import { ApiError } from "@/api/client";
import type { ArchiveOut } from "@/api/types";
import { TimelapsePlayer } from "@/components/print/TimelapsePlayer";
import { Button } from "@/components/ui/button";
import { DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";
import { Label } from "@/components/ui/label";

const SPEEDS = [0.25, 0.5, 1, 1.5, 2, 3, 4];
const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";

function Editor({ archive, onDone, onState }: { archive: ArchiveOut; onDone: () => void; onState: (s: { dirty: boolean; busy: boolean }) => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const info = useTimelapseInfo(archive.id);
  const thumbs = useTimelapseThumbnails(archive.id, 12);
  const process = useProcessTimelapse(archive.id);
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState<number | null>(null);
  const [speed, setSpeed] = useState(1);
  const [mode, setMode] = useState<"replace" | "new">("replace");
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useTimelapseJob(archive.id, jobId);
  const duration = info.data?.duration_s ?? 0;
  const effEnd = end ?? duration;
  const state = job.data?.state;

  useEffect(() => {
    if (state === "done") {
      toast.success(t("archiveX.timelapse.done"));
      void qc.invalidateQueries({ queryKey: ["archives"] });
      onDone();
    } else if (state === "error" || job.isError) {
      toast.error(job.data?.error ?? t("archiveX.timelapse.failed"));
      setJobId(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, job.isError]);

  const busy = process.isPending || (jobId != null && state !== "error" && !job.isError);
  const dirty = start !== 0 || end !== null || speed !== 1 || mode !== "replace";
  useEffect(() => {
    onState({ dirty, busy });
  }, [dirty, busy, onState]);
  const submit = () =>
    process.mutate(
      { trim_start: start, ...(end != null && end < duration ? { trim_end: end } : {}), speed, save_mode: mode },
      {
        onSuccess: (r) => setJobId(r.job_id),
        onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("archiveX.timelapse.failed")),
      },
    );

  return (
    <div className="space-y-4 text-sm">
      <TimelapsePlayer
        archiveId={archive.id}
        poster={archive.has_snapshot ? archiveSnapshotUrl(archive.id) : undefined}
        className="aspect-video"
      />
      {info.data?.frames ? (
        <p className="text-xs text-muted-foreground" data-testid="layer-info">
          {t("archiveX.timelapse.layerInfo", { frames: info.data.frames, layers: info.data.layers ?? info.data.frames })}
        </p>
      ) : null}
      {thumbs.data?.frames.length ? (
        <div className="flex gap-0.5 overflow-x-auto" data-testid="thumb-strip">
          {thumbs.data.frames.map((src, i) => (
            <img key={i} src={src} alt="" className="h-12 flex-1 rounded-sm object-cover" />
          ))}
        </div>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="tl-start">{t("archiveX.timelapse.start", { s: start.toFixed(1) })}</Label>
          <input
            id="tl-start"
            type="range"
            min={0}
            max={duration}
            step={0.1}
            value={start}
            disabled={!duration}
            onChange={(e) => setStart(Math.min(Number(e.target.value), effEnd))}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="tl-end">{t("archiveX.timelapse.end", { s: effEnd.toFixed(1) })}</Label>
          <input
            id="tl-end"
            type="range"
            min={0}
            max={duration}
            step={0.1}
            value={effEnd}
            disabled={!duration}
            onChange={(e) => setEnd(Math.max(Number(e.target.value), start))}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="tl-speed">{t("archiveX.timelapse.speed")}</Label>
          <select id="tl-speed" className={selectClass} value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
            {SPEEDS.map((s) => (
              <option key={s} value={s}>
                {s}x
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="tl-mode">{t("archiveX.timelapse.saveMode")}</Label>
          <select
            id="tl-mode"
            className={selectClass}
            value={mode}
            onChange={(e) => setMode(e.target.value as "replace" | "new")}
          >
            <option value="replace">{t("archiveX.timelapse.replace")}</option>
            <option value="new">{t("archiveX.timelapse.new")}</option>
          </select>
        </div>
      </div>
      <Button onClick={submit} disabled={busy || !duration}>
        {busy ? t("archiveX.timelapse.processing") : t("archiveX.timelapse.process")}
      </Button>
    </div>
  );
}

export function TimelapseEditorDialog({
  archive,
  open,
  onOpenChange,
}: {
  archive: ArchiveOut;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const [state, setState] = useState({ dirty: false, busy: false });
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} dirty={state.dirty} busy={state.busy} className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t("archiveX.timelapse.title")}</DialogTitle>
          <DialogDescription>{t("archiveX.timelapse.desc")}</DialogDescription>
        </DialogHeader>
        <Editor archive={archive} onDone={() => onOpenChange(false)} onState={setState} />
    </FormDialog>
  );
}
