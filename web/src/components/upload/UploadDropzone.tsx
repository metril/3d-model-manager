import { useCallback, useEffect, useRef, useState } from "react";
import { UploadCloudIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { api } from "@/api/client";
import type { JobOut } from "@/api/types";
import { DuplicateUploadError, UploadAbortedError, uploadFile } from "@/api/upload";
import { Button } from "@/components/ui/button";
import { UploadQueueItem, type QueueItem } from "@/components/upload/UploadQueueItem";
import { useEvents } from "@/hooks/eventsContext";
import { resolveDroppedFiles, type DroppedFile } from "@/lib/droppedFiles";
import { randomId } from "@/lib/randomId";

/** Where a batch's files land. Resolved lazily via `resolveTarget` (below)
 * rather than passed as a plain prop, so this one component serves both
 * call sites: UploadPage.tsx (which may need to create a brand-new model on
 * the first batch, asynchronously, before a target exists) and
 * FilesTab.tsx's "Add files" action (whose target -- the model's current
 * revision -- is already known upfront). */
export interface UploadTarget {
  modelId: number;
  revisionId: number;
}

/** Thin wrapper around a `Map<string, "done" | "failed">` (Task 8, M2-Minor
 * 5 fold; moved here unmodified by Task 10's UploadPage.tsx extraction) --
 * a dedicated class rather than a bare `Map` so tests can spy on `.delete`
 * calls to verify the eviction below actually fires, without hooking the
 * global `Map.prototype` and picking up every unrelated `Map` React/Radix/
 * TanStack Query use internally. */
export class TerminalEventMap {
  /** Orphan records (an event whose upload never claims it) are evicted
   * oldest-first past this size. */
  static readonly MAX_ENTRIES = 500;
  private readonly entries = new Map<string, "done" | "failed">();

  get size(): number {
    return this.entries.size;
  }

  get(jobId: string): "done" | "failed" | undefined {
    return this.entries.get(jobId);
  }

  set(jobId: string, state: "done" | "failed"): void {
    this.entries.delete(jobId);
    this.entries.set(jobId, state);
    while (this.entries.size > TerminalEventMap.MAX_ENTRIES) {
      const oldest = this.entries.keys().next().value;
      if (oldest === undefined) break;
      this.entries.delete(oldest);
    }
  }

  delete(jobId: string): void {
    this.entries.delete(jobId);
  }
}

/** Prefixes a dropped/picked relative path with a destination folder ("" = revision root). */
function withPrefix(prefix: string | undefined, relPath: string): string {
  const p = (prefix ?? "").split("/").filter(Boolean).join("/");
  return p ? `${p}/${relPath}` : relPath;
}

/** Parallel uploads per batch. */
const UPLOAD_CONCURRENCY = 3;
const PROGRESS_THROTTLE_MS = 100;
/** No SSE terminal event this long after the PUT -> fall back to polling the job. */
const PROCESSING_POLL_AFTER_MS = 60_000;
const PROCESSING_POLL_EVERY_MS = 10_000;

function toQueueItems(files: DroppedFile[], prefix?: string): QueueItem[] {
  return files.map((entry) => ({
    id: randomId(),
    file: entry.file,
    relPath: withPrefix(prefix, entry.relPath),
    size: entry.file.size,
    progress: 0,
    status: "pending",
  }));
}

export interface UploadDropzoneProps {
  /** Resolves the target for the CURRENT batch; called once per
   * "Upload" click, not once per file. Returning `null` silently aborts
   * the batch -- mirrors UploadPage.tsx's pre-extraction behavior of
   * bailing out of `handleStartUpload` when no target could be resolved
   * (a global mutation-error toast, if any, already surfaced the failure
   * upstream of this callback). */
  resolveTarget: () => Promise<UploadTarget | null>;
  /** Extra external condition that also disables the Upload button, on top
   * of the internal pendingCount/isUploading checks -- UploadPage.tsx uses
   * this for its TargetPicker's "a target is chosen" requirement, which
   * FilesTab.tsx (whose target is always the model's current revision)
   * doesn't need. */
  disabled?: boolean;
  /** Invoked once after a batch's upload loop finishes (not once per file),
   * so callers can invalidate whichever query reflects the newly-added
   * files. */
  onUploadComplete?: () => void;
  /** Mirrors this component's internal `isUploading` state to the caller --
   * UploadPage.tsx uses it to keep disabling its TargetPicker for the
   * duration of a batch, exactly as it did before this component was
   * extracted (preventing a target switch mid-upload). */
  onUploadingChange?: (isUploading: boolean) => void;
  /** Folder every added file lands in (the explorer's current folder); applied when files are added. */
  relPathPrefix?: string;
}

/**
 * Reusable drag/drop file queue + "Upload" trigger (Task 10 extraction from
 * UploadPage.tsx). Owns the queue state/handlers and the SSE terminal-event
 * race handling (see `TerminalEventMap` above); the caller only supplies
 * how to resolve a target and what to do once a batch completes.
 */
export function UploadDropzone({
  resolveTarget,
  disabled,
  onUploadComplete,
  onUploadingChange,
  relPathPrefix,
}: UploadDropzoneProps) {
  const { t } = useTranslation();
  const [queue, setQueue] = useState<QueueItem[]>([]);
  // The batch loop reads the queue through this ref, so items added or
  // edited while it runs are seen instead of a stale snapshot.
  const queueRef = useRef(queue);
  queueRef.current = queue;
  const dragDepth = useRef(0);
  const [dragActive, setDragActive] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const events = useEvents();
  // The upload's PUT response (carrying `jobId`) and its `job.updated` SSE
  // event race independently: the pipeline can finish (and publish its
  // terminal event) before the PUT even resolves. Recording every terminal
  // event here means the jobId lookup below survives regardless of which
  // one lands first, instead of silently dropping an event that arrived for
  // a jobId no queue item had yet.
  const seenTerminal = useRef(new TerminalEventMap());
  // The last-resolved upload target, remembered so a "Upload anyway" retry
  // (fired well after `handleStartUpload`'s batch loop has moved on) still
  // knows where to PUT the file (R11-C item 18).
  const targetRef = useRef<UploadTarget | null>(null);
  // Claimed synchronously (before the first await) so a double-click can't
  // start two batches; `isUploading` state only flips after `resolveTarget`.
  const inFlightRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  // Per-item controllers (single-item cancel) and the ids cancelled that way.
  const itemAborts = useRef(new Map<string, AbortController>());
  const canceledItems = useRef(new Set<string>());
  const lastProgress = useRef(new Map<string, number>());
  // Uploads currently running (batch workers + single retries): the batch
  // "uploading" state and onUploadComplete follow this counter.
  const activeRuns = useRef(0);
  const mounted = useRef(true);
  const callbacks = useRef({ onUploadComplete, onUploadingChange });
  callbacks.current = { onUploadComplete, onUploadingChange };

  // Warn before the tab closes mid-upload.
  useEffect(() => {
    if (!isUploading) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isUploading]);

  useEffect(() => {
    mounted.current = true;
    const aborts = itemAborts.current;
    return () => {
      mounted.current = false;
      abortRef.current?.abort();
      for (const c of aborts.values()) c.abort();
    };
  }, []);

  const applyTerminal = useCallback((jobId: string, terminalState: "done" | "failed") => {
      // The eviction decision has to be made *inside* the updater, off
      // whatever `prev` React actually hands it -- reading a flag set by
      // the updater immediately after calling `setQueue` doesn't work
      // reliably, because React doesn't always invoke a `setState` updater
      // synchronously (e.g. with another update already in flight, it can
      // defer running this one until the next render pass), so such a flag
      // can still read as its initial value here.
      setQueue((prev) => {
        const matchIndex = prev.findIndex((item) => item.jobId === jobId);
        if (matchIndex === -1) {
          // The event raced ahead of the upload's PUT response attaching
          // this jobId to a queue item -- keep the record so
          // `handleStartUpload`'s post-await consultation below can still
          // find it once the response resolves, which evicts it there.
          seenTerminal.current.set(jobId, terminalState);
          return prev;
        }
        // Applied directly to the item that already carried this jobId --
        // no future lookup will ever need the pending record again, so
        // evict it now instead of letting `seenTerminal` grow for the rest
        // of the tab's life (M2-Minor 5).
        seenTerminal.current.delete(jobId);
        return prev.map((item, index) => {
          if (index !== matchIndex) return item;
          return terminalState === "done"
            ? { ...item, status: "stored" }
            : { ...item, status: "failed", error: t("upload.processingFailed") };
        });
      });
  }, [t]);

  useEffect(() => {
    return events.subscribe((event) => {
      if (event.state !== "done" && event.state !== "failed") return;
      applyTerminal(event.job_id, event.state);
    });
  }, [events, applyTerminal]);

  /** Fallback when the SSE terminal event never arrives (dropped stream):
   * after a grace period, poll the job list until the job settles. */
  const watchProcessing = useCallback(
    (itemId: string, jobId: string) => {
      void (async () => {
        await new Promise((r) => setTimeout(r, PROCESSING_POLL_AFTER_MS));
        for (;;) {
          if (!mounted.current) return;
          const cur = queueRef.current.find((i) => i.id === itemId);
          if (!cur || cur.status !== "processing" || cur.jobId !== jobId) return;
          try {
            const jobs = await api.get<JobOut[]>("/jobs?limit=200");
            const job = jobs.find((j) => j.id === jobId);
            if (job && (job.state === "done" || job.state === "failed")) {
              applyTerminal(jobId, job.state);
              return;
            }
          } catch {
            /* transient: keep polling */
          }
          await new Promise((r) => setTimeout(r, PROCESSING_POLL_EVERY_MS));
        }
      })();
    },
    [applyTerminal],
  );

  function addFiles(files: DroppedFile[]) {
    if (files.length === 0) return;
    setQueue((prev) => [...prev, ...toQueueItems(files, relPathPrefix)]);
  }

  function updateItem(id: string, patch: Partial<QueueItem>) {
    setQueue((prev) => prev.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }

  function removeItem(id: string) {
    setQueue((prev) => prev.filter((item) => item.id !== id));
  }

  function handleFileInputChange(event: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []).map((file) => ({
      file,
      relPath: file.webkitRelativePath || file.name,
    }));
    addFiles(files);
    event.target.value = "";
  }

  async function handleDrop(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragActive(false);
    const files = await resolveDroppedFiles(event.dataTransfer);
    addFiles(files);
  }

  const pendingCount = queue.filter((item) => item.status === "pending").length;

  function beginRun() {
    if (activeRuns.current++ === 0) {
      setIsUploading(true);
      callbacks.current.onUploadingChange?.(true);
    }
  }
  function endRun() {
    if (--activeRuns.current === 0) {
      if (mounted.current) setIsUploading(false);
      callbacks.current.onUploadingChange?.(false);
      callbacks.current.onUploadComplete?.();
    }
  }

  /** Uploads a single queue item against `target`, updating its status as
   * it goes. Shared by the batch workers and the single-item retries
   * ("Upload anyway", "Retry"). `batchSignal` aborts every item at once;
   * each item also has its own controller for a per-item cancel. */
  async function uploadOne(item: QueueItem, target: UploadTarget, opts: { allowDuplicate?: boolean; batchSignal?: AbortSignal } = {}) {
    const ic = new AbortController();
    itemAborts.current.set(item.id, ic);
    canceledItems.current.delete(item.id);
    const onBatchAbort = () => ic.abort();
    opts.batchSignal?.addEventListener("abort", onBatchAbort, { once: true });
    updateItem(item.id, { status: "uploading", progress: 0, error: undefined, duplicate: undefined });
    try {
      const result = await uploadFile(
        {
          modelId: target.modelId,
          revisionId: target.revisionId,
          relPath: item.relPath,
          file: item.file,
          ...(opts.allowDuplicate ? { allowDuplicate: true } : {}),
        },
        {
          // Throttled: XHR progress can fire far faster than React should re-render.
          onProgress: (loaded, total) => {
            const now = Date.now();
            if (loaded < total && now - (lastProgress.current.get(item.id) ?? 0) < PROGRESS_THROTTLE_MS) return;
            lastProgress.current.set(item.id, now);
            updateItem(item.id, { progress: total > 0 ? Math.round((loaded / total) * 100) : 0 });
          },
          signal: ic.signal,
        },
      );
      updateItem(item.id, { status: "processing", progress: 100, jobId: result.job_id });
      const terminal = seenTerminal.current.get(result.job_id);
      if (terminal) {
        // Consumed -- evict so this record doesn't linger forever (M2-Minor 5).
        seenTerminal.current.delete(result.job_id);
        if (terminal === "done") {
          updateItem(item.id, { status: "stored" });
        } else {
          updateItem(item.id, { status: "failed", error: t("upload.processingFailed") });
        }
      } else {
        watchProcessing(item.id, result.job_id);
      }
    } catch (error) {
      if (error instanceof UploadAbortedError) {
        // A per-item cancel parks the item as failed (retryable); a whole-batch
        // cancel returns it to pending.
        if (canceledItems.current.has(item.id)) {
          updateItem(item.id, { status: "failed", progress: 0, error: t("upload.canceled") });
        } else {
          updateItem(item.id, { status: "pending", progress: 0 });
        }
        return;
      }
      if (error instanceof DuplicateUploadError) {
        updateItem(item.id, {
          status: "duplicate",
          duplicate: { existing: error.existing, suggestedName: error.suggestedName },
        });
        return;
      }
      updateItem(item.id, {
        status: "failed",
        error: error instanceof Error ? error.message : t("upload.failed"),
      });
    } finally {
      opts.batchSignal?.removeEventListener("abort", onBatchAbort);
      itemAborts.current.delete(item.id);
      lastProgress.current.delete(item.id);
    }
  }

  async function handleStartUpload() {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    try {
      const target = await resolveTarget();
      if (!target) return;
      targetRef.current = target;
      const controller = new AbortController();
      abortRef.current = controller;
      beginRun();
      try {
        const claimed = new Set<string>();
        const worker = async () => {
          for (;;) {
            if (controller.signal.aborted) return;
            const next = queueRef.current.find((i) => i.status === "pending" && !claimed.has(i.id));
            if (!next) return;
            claimed.add(next.id);
            await uploadOne(next, target, { batchSignal: controller.signal });
          }
        };
        await Promise.all(Array.from({ length: UPLOAD_CONCURRENCY }, worker));
      } finally {
        endRun();
      }
    } finally {
      inFlightRef.current = false;
    }
  }

  /** Re-runs one item outside the batch (same busy state + completion callback). */
  function runSingle(item: QueueItem, allowDuplicate: boolean) {
    const target = targetRef.current;
    if (!target) return;
    beginRun();
    void uploadOne(item, target, { allowDuplicate }).finally(endRun);
  }

  function handleUploadAnyway(item: QueueItem) {
    runSingle(item, true);
  }

  function handleRetry(item: QueueItem) {
    runSingle(item, false);
  }

  function handleCancelItem(item: QueueItem) {
    canceledItems.current.add(item.id);
    itemAborts.current.get(item.id)?.abort();
  }

  return (
    <div className="space-y-3">
      <div
        onDragEnter={() => {
          dragDepth.current += 1;
          setDragActive(true);
        }}
        onDragOver={(event) => {
          event.preventDefault();
          setDragActive(true);
        }}
        // Child elements fire their own enter/leave pairs: count depth so the
        // highlight only clears when the pointer really leaves the zone.
        onDragLeave={() => {
          dragDepth.current = Math.max(0, dragDepth.current - 1);
          if (dragDepth.current === 0) setDragActive(false);
        }}
        onDrop={(event) => {
          dragDepth.current = 0;
          void handleDrop(event);
        }}
        className={`flex flex-col items-center gap-2 rounded-xl border-2 border-dashed p-10 text-center transition-colors ${
          dragActive ? "border-primary bg-primary/5" : "border-border"
        }`}
      >
        <UploadCloudIcon className="size-8 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">{t("upload.dropHint")}</p>
        <Button type="button" variant="outline" onClick={() => fileInputRef.current?.click()}>
          {t("upload.browse")}
        </Button>
        <input ref={fileInputRef} type="file" multiple hidden onChange={handleFileInputChange} />
      </div>

      {queue.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">{t("upload.queue", { count: queue.length })}</h3>
            <div className="flex gap-2">
              {isUploading && (
                <Button type="button" variant="outline" onClick={() => abortRef.current?.abort()}>
                  {t("common.cancel")}
                </Button>
              )}
              <Button
                type="button"
                onClick={() => void handleStartUpload()}
                disabled={disabled || isUploading || pendingCount === 0}
              >
                {isUploading ? t("upload.uploading") : `${t("upload.upload")} ${pendingCount || ""}`.trim()}
              </Button>
            </div>
          </div>
          <div className="space-y-2">
            {queue.map((item) => (
              <UploadQueueItem
                key={item.id}
                item={item}
                onRelPathChange={(relPath) => updateItem(item.id, { relPath })}
                onRemove={() => removeItem(item.id)}
                onUploadAnyway={() => handleUploadAnyway(item)}
                onCancel={() => handleCancelItem(item)}
                onRetry={() => handleRetry(item)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
