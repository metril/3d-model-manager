/**
 * Follows the slice jobs started via `SliceDialog`: one watcher per tracked
 * job polls it (`useSliceJob`; SSE `slice.updated` refreshes it sooner) and
 * keeps a single sonner toast in sync -- progress with Cancel, then on
 * success the resulting file (open / download / send to printer), and on
 * success or failure a "View log" dialog. Mounted once in the app shell so a toast outlives the
 * dialog or page that started the slice.
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";

import { useCancelSliceJob, useSliceJob } from "@/api/slicing";
import type { SliceJob } from "@/api/types";
import { PrintDialog } from "@/components/print/PrintDialog";
import { SliceLogDialog } from "@/components/slice/SliceLogDialog";
import { untrackSlice, useTrackedSlices, type TrackedSlice } from "@/components/slice/sliceTracker";
import { Button } from "@/components/ui/button";
import { humanizeDuration } from "@/lib/format";

const ACTIVE = ["queued", "running"];

export function SliceToast({
  fileName,
  job,
  onCancel,
  onOpen,
  onSend,
  onLog,
  onDismiss,
}: {
  fileName: string;
  job: SliceJob | undefined;
  onCancel: () => void;
  onOpen: () => void;
  onSend: () => void;
  onLog: () => void;
  onDismiss: () => void;
}) {
  const state = job?.state ?? "queued";
  const meta = job?.meta;
  return (
    <div
      role="status"
      className="flex w-[356px] max-w-full flex-col gap-2 rounded-lg border bg-popover p-3 text-sm text-popover-foreground shadow-lg"
    >
      <div className="flex items-start justify-between gap-2">
        <p className="min-w-0 break-words font-medium">
          {state === "queued" ? `Slicing ${fileName}: waiting for the slicer…` : null}
          {state === "running" ? `Slicing ${fileName}…` : null}
          {state === "done" ? `Sliced ${fileName}` : null}
          {state === "failed" ? `Slicing ${fileName} failed` : null}
          {state === "cancelled" ? `Slicing ${fileName} cancelled` : null}
        </p>
        {!ACTIVE.includes(state) ? (
          <button type="button" aria-label="Dismiss" className="text-muted-foreground hover:text-foreground" onClick={onDismiss}>
            ×
          </button>
        ) : null}
      </div>
      {state === "done" && meta ? (
        <p className="text-xs text-muted-foreground">
          {meta.print_time_s != null ? humanizeDuration(meta.print_time_s) : null}
          {meta.filament_g != null ? ` · ${Math.round(meta.filament_g)} g` : null}
          {meta.plate_count ? ` · ${meta.plate_count} plate${meta.plate_count === 1 ? "" : "s"}` : null}
        </p>
      ) : null}
      {state === "failed" && job?.error ? <p className="text-xs text-destructive">{job.error}</p> : null}
      <div className="flex flex-wrap gap-2">
        {ACTIVE.includes(state) ? (
          <Button type="button" size="sm" variant="outline" onClick={onCancel}>
            Cancel
          </Button>
        ) : null}
        {state === "done" && job?.sliced_file_id != null ? (
          <>
            <Button type="button" size="sm" variant="outline" onClick={onOpen}>
              Open file
            </Button>
            <Button type="button" size="sm" variant="outline" asChild>
              <a href={`/api/files/${job.sliced_file_id}/download`}>Download</a>
            </Button>
            <Button type="button" size="sm" onClick={onSend}>
              Send to printer
            </Button>
          </>
        ) : null}
        {state === "failed" || state === "done" ? (
          <Button type="button" size="sm" variant="outline" onClick={onLog}>
            View log
          </Button>
        ) : null}
      </div>
    </div>
  );
}

function SliceWatcher({
  item,
  onLog,
  onSend,
}: {
  item: TrackedSlice;
  onLog: (job: SliceJob) => void;
  onSend: (job: SliceJob) => void;
}) {
  const navigate = useNavigate();
  const query = useSliceJob(item.id);
  const job = query.data;
  const loadError = query.isError && !job ? query.error : null;
  const cancel = useCancelSliceJob();
  const toastId = `slice-${item.id}`;
  const state = job?.state ?? "queued";
  const active = ACTIVE.includes(state);
  // The toast is rebuilt from the latest props on every change; callbacks
  // read them through a ref so a stale closure never survives in the toast.
  const latest = useRef({ job, cancel, navigate, onLog, onSend });
  latest.current = { job, cancel, navigate, onLog, onSend };

  // The job can't be loaded (deleted with its file, no access): stop
  // following it instead of showing "waiting…" forever.
  useEffect(() => {
    if (!loadError) return;
    toast.dismiss(toastId);
    toast.error(`Slicing ${item.fileName}: ${loadError instanceof Error ? loadError.message : "job not found"}`);
    untrackSlice(item.id);
  }, [loadError, item.id, item.fileName, toastId]);

  useEffect(() => {
    if (loadError) return;
    const dismiss = () => {
      toast.dismiss(toastId);
      untrackSlice(item.id);
    };
    toast.custom(
      () => (
        <SliceToast
          fileName={item.fileName}
          job={latest.current.job}
          onCancel={() => latest.current.cancel.mutate(item.id)}
          onOpen={() => {
            const j = latest.current.job;
            if (j?.model_slug) void latest.current.navigate({ to: "/models/$slug", params: { slug: j.model_slug } });
          }}
          onSend={() => latest.current.job && latest.current.onSend(latest.current.job)}
          onLog={() => latest.current.job && latest.current.onLog(latest.current.job)}
          onDismiss={dismiss}
        />
      ),
      { id: toastId, duration: active ? Infinity : state === "done" ? 60_000 : 30_000, onAutoClose: () => untrackSlice(item.id) },
    );
    // `job` identity changes on every poll; the toast only depends on state
    // and the fields rendered from it.
  }, [item, toastId, state, active, loadError, job?.sliced_file_id, job?.error, job?.meta]);

  return null;
}

export function SliceJobsHost() {
  const tracked = useTrackedSlices();
  const [logJob, setLogJob] = useState<SliceJob | null>(null);
  const [printJob, setPrintJob] = useState<SliceJob | null>(null);
  return (
    <>
      {tracked.map((t) => (
        <SliceWatcher key={t.id} item={t} onLog={setLogJob} onSend={setPrintJob} />
      ))}
      <SliceLogDialog
        open={logJob != null}
        onOpenChange={(o) => !o && setLogJob(null)}
        error={logJob?.error}
        log={logJob?.log_tail}
      />
      {printJob?.sliced_file_id != null ? (
        <PrintDialog
          open
          onOpenChange={(o) => !o && setPrintJob(null)}
          source={{ kind: "file", fileId: printJob.sliced_file_id, format: "gcode_3mf" }}
        />
      ) : null}
    </>
  );
}
