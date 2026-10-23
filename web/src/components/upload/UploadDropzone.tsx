import { useEffect, useRef, useState } from "react";
import { UploadCloudIcon } from "lucide-react";

import { DuplicateUploadError, uploadFile } from "@/api/upload";
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
  private readonly entries = new Map<string, "done" | "failed">();

  get(jobId: string): "done" | "failed" | undefined {
    return this.entries.get(jobId);
  }

  set(jobId: string, state: "done" | "failed"): void {
    this.entries.set(jobId, state);
  }

  delete(jobId: string): void {
    this.entries.delete(jobId);
  }
}

function toQueueItems(files: DroppedFile[]): QueueItem[] {
  return files.map((entry) => ({
    id: randomId(),
    file: entry.file,
    relPath: entry.relPath,
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
}: UploadDropzoneProps) {
  const [queue, setQueue] = useState<QueueItem[]>([]);
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

  useEffect(() => {
    return events.subscribe((event) => {
      if (event.state !== "done" && event.state !== "failed") return;
      // Narrowed to a local so the type survives into the nested `setQueue`
      // updater below (TS doesn't carry a narrowing on `event.state` itself
      // through a closure).
      const terminalState = event.state;

      // The eviction decision has to be made *inside* the updater, off
      // whatever `prev` React actually hands it -- reading a flag set by
      // the updater immediately after calling `setQueue` doesn't work
      // reliably, because React doesn't always invoke a `setState` updater
      // synchronously (e.g. with another update already in flight, it can
      // defer running this one until the next render pass), so such a flag
      // can still read as its initial value here.
      setQueue((prev) => {
        const matchIndex = prev.findIndex((item) => item.jobId === event.job_id);
        if (matchIndex === -1) {
          // The event raced ahead of the upload's PUT response attaching
          // this jobId to a queue item -- keep the record so
          // `handleStartUpload`'s post-await consultation below can still
          // find it once the response resolves, which evicts it there.
          seenTerminal.current.set(event.job_id, terminalState);
          return prev;
        }
        // Applied directly to the item that already carried this jobId --
        // no future lookup will ever need the pending record again, so
        // evict it now instead of letting `seenTerminal` grow for the rest
        // of the tab's life (M2-Minor 5).
        seenTerminal.current.delete(event.job_id);
        return prev.map((item, index) => {
          if (index !== matchIndex) return item;
          return terminalState === "done"
            ? { ...item, status: "stored" }
            : { ...item, status: "failed", error: "Processing failed" };
        });
      });
    });
  }, [events]);

  function addFiles(files: DroppedFile[]) {
    if (files.length === 0) return;
    setQueue((prev) => [...prev, ...toQueueItems(files)]);
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

  /** Uploads a single queue item against `target`, updating its status as
   * it goes. Shared by the batch loop below and the "Upload anyway" retry
   * (which re-runs just this one item, outside the batch). */
  async function uploadOne(item: QueueItem, target: UploadTarget, allowDuplicate = false) {
    updateItem(item.id, { status: "uploading", progress: 0, duplicate: undefined });
    try {
      const result = await uploadFile(
        {
          modelId: target.modelId,
          revisionId: target.revisionId,
          relPath: item.relPath,
          file: item.file,
          ...(allowDuplicate ? { allowDuplicate: true } : {}),
        },
        {
          onProgress: (loaded, total) =>
            updateItem(item.id, { progress: total > 0 ? Math.round((loaded / total) * 100) : 0 }),
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
          updateItem(item.id, { status: "failed", error: "Processing failed" });
        }
      }
    } catch (error) {
      if (error instanceof DuplicateUploadError) {
        updateItem(item.id, {
          status: "duplicate",
          duplicate: { existing: error.existing, suggestedName: error.suggestedName },
        });
        return;
      }
      updateItem(item.id, {
        status: "failed",
        error: error instanceof Error ? error.message : "Upload failed",
      });
    }
  }

  async function handleStartUpload() {
    const target = await resolveTarget();
    if (!target) return;
    targetRef.current = target;
    setIsUploading(true);
    onUploadingChange?.(true);

    for (const item of queue) {
      if (item.status !== "pending") continue;
      await uploadOne(item, target);
    }

    setIsUploading(false);
    onUploadingChange?.(false);
    onUploadComplete?.();
  }

  function handleUploadAnyway(item: QueueItem) {
    const target = targetRef.current;
    if (!target) return;
    void uploadOne(item, target, true);
  }

  return (
    <div className="space-y-3">
      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={(event) => void handleDrop(event)}
        className={`flex flex-col items-center gap-2 rounded-xl border-2 border-dashed p-10 text-center transition-colors ${
          dragActive ? "border-primary bg-primary/5" : "border-border"
        }`}
      >
        <UploadCloudIcon className="size-8 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">Drag and drop files or folders here</p>
        <Button type="button" variant="outline" onClick={() => fileInputRef.current?.click()}>
          Browse files
        </Button>
        <input ref={fileInputRef} type="file" multiple hidden onChange={handleFileInputChange} />
      </div>

      {queue.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">Queue ({queue.length})</h3>
            <Button
              type="button"
              onClick={() => void handleStartUpload()}
              disabled={disabled || isUploading || pendingCount === 0}
            >
              {isUploading ? "Uploading..." : `Upload ${pendingCount || ""}`.trim()}
            </Button>
          </div>
          <div className="space-y-2">
            {queue.map((item) => (
              <UploadQueueItem
                key={item.id}
                item={item}
                onRelPathChange={(relPath) => updateItem(item.id, { relPath })}
                onRemove={() => removeItem(item.id)}
                onUploadAnyway={() => handleUploadAnyway(item)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
