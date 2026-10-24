/**
 * T4: the Batches tab on the queue page -- table with progress, create/edit
 * dialog (`sm:max-w-lg`), Dispatch / Ungroup / Delete row actions and a
 * detail drawer listing the batch's queue entries (`GET /queue?batch_id=`).
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useModelSearchQuery } from "@/api/library";
import { usePrinters } from "@/api/printers";
import {
  useBatches,
  useCreateBatch,
  useDeleteBatch,
  useDispatchBatch,
  usePatchBatch,
  useQueue,
  useUngroupBatch,
} from "@/api/queue";
import type { Batch, BatchIn, BatchPatch } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { QueueStateChip } from "@/pages/QueuePage";
import { SchedulingFields } from "@/components/print/SchedulingSection";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { draftToFields, EMPTY_SCHEDULE, type ScheduleDraft } from "@/lib/schedule";

export function BatchProgressBar({ batch }: { batch: Batch }) {
  const { t } = useTranslation();
  const p = batch.progress;
  const total = Math.max(p.total, 1);
  const label = t("queueSched.batches.progress", {
    done: p.done,
    failed: p.failed,
    skipped: p.skipped,
    waiting: p.waiting,
    total: p.total,
  });
  const seg = (n: number, cls: string, key: string) =>
    n > 0 ? <div key={key} data-seg={key} className={cls} style={{ width: `${(n / total) * 100}%` }} /> : null;
  return (
    <div
      role="img"
      aria-label={label}
      title={label}
      className="flex h-2 w-40 overflow-hidden rounded-full bg-muted"
    >
      {seg(p.done, "bg-state-finished", "done")}
      {seg(p.failed, "bg-destructive", "failed")}
      {seg(p.skipped, "bg-amber-500", "skipped")}
    </div>
  );
}

interface PlateRow {
  plate: string;
  qty: string;
}

function BatchDialog({
  open,
  onOpenChange,
  batch,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  batch: Batch | null;
}) {
  const { t } = useTranslation();
  const printers = usePrinters();
  const create = useCreateBatch();
  const patch = usePatchBatch();
  const editing = batch != null;
  const structural = !editing || batch.status === "draft";

  const [name, setName] = useState("");
  const [query, setQuery] = useState("");
  const [modelId, setModelId] = useState<number | null>(null);
  const [modelName, setModelName] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [printerId, setPrinterId] = useState("");
  const [due, setDue] = useState("");
  const [notes, setNotes] = useState("");
  const [plates, setPlates] = useState<PlateRow[]>([]);
  const [sched, setSched] = useState<ScheduleDraft>(EMPTY_SCHEDULE);
  const [error, setError] = useState<string | null>(null);
  const search = useModelSearchQuery(query);

  useEffect(() => {
    if (!open) return;
    setName(batch?.name ?? "");
    setQuery("");
    setModelId(batch?.model_id ?? null);
    setModelName(batch?.model_id != null ? `#${batch.model_id}` : "");
    setQuantity(String(batch?.quantity ?? 1));
    setPrinterId(batch?.printer_id != null ? String(batch.printer_id) : "");
    setDue(batch?.due_date ?? "");
    setNotes(batch?.notes ?? "");
    setPlates(Object.entries(batch?.plate_targets ?? {}).map(([plate, qty]) => ({ plate, qty: String(qty) })));
    setSched(EMPTY_SCHEDULE);
    setError(null);
  }, [open, batch]);

  const pending = create.isPending || patch.isPending;

  function plateTargets(): Record<string, number> {
    const out: Record<string, number> = {};
    for (const r of plates) {
      const p = Number(r.plate);
      const q = Number(r.qty);
      if (Number.isInteger(p) && p >= 1 && Number.isInteger(q) && q >= 1) out[String(p)] = q;
    }
    return out;
  }

  function submit() {
    const qty = Number(quantity);
    if (!name.trim()) return setError(t("queueSched.batches.nameRequired"));
    if (!Number.isInteger(qty) || qty < 1 || qty > 500) return setError(t("queueSched.batches.qtyRange"));
    if (!editing && modelId == null) return setError(t("queueSched.batches.needSource"));
    setError(null);
    const done = (msg: string) => () => {
      toast.success(msg);
      onOpenChange(false);
    };
    if (editing) {
      const body: BatchPatch = { name: name.trim(), notes, due_date: due || null };
      if (structural) {
        body.quantity = qty;
        body.plate_targets = plateTargets();
        body.printer_id = printerId ? Number(printerId) : null;
      }
      patch.mutate({ batchId: batch.id, body }, { onSuccess: done(t("queueSched.batches.saved")) });
      return;
    }
    const body: BatchIn = {
      name: name.trim(),
      model_id: modelId,
      quantity: qty,
      printer_id: printerId ? Number(printerId) : null,
      due_date: due || null,
      notes,
      plate_targets: plateTargets(),
      ...draftToFields(sched),
    };
    create.mutate(body, { onSuccess: done(t("queueSched.batches.created")) });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{editing ? t("queueSched.batches.editTitle") : t("queueSched.batches.createTitle")}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="batch-name">{t("queueSched.batches.name")}</Label>
            <Input id="batch-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          {!editing ? (
            <div className="space-y-1.5">
              <Label htmlFor="batch-model">{t("queueSched.batches.source")}</Label>
              {modelId != null ? (
                <div className="flex items-center gap-2 text-sm">
                  <Badge variant="secondary">{modelName}</Badge>
                  <button type="button" className="text-xs underline" onClick={() => setModelId(null)}>
                    {t("queueSched.batches.edit")}
                  </button>
                </div>
              ) : (
                <>
                  <Input
                    id="batch-model"
                    placeholder={t("queueSched.batches.searchModel")}
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                  <ul className="max-h-32 overflow-y-auto text-sm">
                    {(search.data?.items ?? []).map((m) => (
                      <li key={m.id}>
                        <button
                          type="button"
                          className="w-full rounded px-2 py-1 text-left hover:bg-muted"
                          onClick={() => {
                            setModelId(m.id);
                            setModelName(m.name);
                          }}
                        >
                          {m.name}
                        </button>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          ) : null}
          <div className="flex flex-wrap gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="batch-qty">{t("queueSched.batches.quantity")}</Label>
              <Input
                id="batch-qty"
                type="number"
                min={1}
                max={500}
                className="w-24"
                disabled={!structural}
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="batch-printer">{t("queueSched.batches.printer")}</Label>
              <select
                id="batch-printer"
                className="h-8 w-44 rounded-md border border-input bg-transparent px-2 text-sm"
                disabled={!structural}
                value={printerId}
                onChange={(e) => setPrinterId(e.target.value)}
              >
                <option value="">{t("queueSched.batches.anyPrinter")}</option>
                {(printers.data ?? []).map((p) => (
                  <option key={p.id} value={String(p.id)}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="batch-due">{t("queueSched.batches.dueDate")}</Label>
              <Input id="batch-due" type="date" className="w-40" value={due} onChange={(e) => setDue(e.target.value)} />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="batch-notes">{t("queueSched.batches.notes")}</Label>
            <Textarea id="batch-notes" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label>{t("queueSched.batches.plateTargets")}</Label>
            <p className="text-xs text-muted-foreground">{t("queueSched.batches.plateTargetsHint")}</p>
            {plates.map((r, i) => (
              <div key={i} className="flex items-center gap-2">
                <Input
                  aria-label={t("queueSched.batches.plate")}
                  type="number"
                  min={1}
                  className="w-20"
                  disabled={!structural}
                  value={r.plate}
                  onChange={(e) => setPlates(plates.map((x, j) => (j === i ? { ...x, plate: e.target.value } : x)))}
                />
                <span>x</span>
                <Input
                  aria-label={t("queueSched.batches.quantity")}
                  type="number"
                  min={1}
                  className="w-20"
                  disabled={!structural}
                  value={r.qty}
                  onChange={(e) => setPlates(plates.map((x, j) => (j === i ? { ...x, qty: e.target.value } : x)))}
                />
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={!structural}
                  onClick={() => setPlates(plates.filter((_, j) => j !== i))}
                >
                  {t("queueSched.preheat_card.remove")}
                </Button>
              </div>
            ))}
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={!structural}
              onClick={() => setPlates([...plates, { plate: String(plates.length + 1), qty: "1" }])}
            >
              {t("queueSched.batches.addPlate")}
            </Button>
          </div>
          {!editing ? (
            <div className="space-y-2">
              <Label>{t("queueSched.batches.defaults")}</Label>
              <SchedulingFields draft={sched} onChange={setSched} idPrefix="batch-sched" />
            </div>
          ) : null}
          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            {t("queueSched.edit.cancel")}
          </Button>
          <Button type="button" disabled={pending} onClick={submit}>
            {t("queueSched.batches.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function BatchDetailSheet({ batchId, onClose }: { batchId: number | null; onClose: () => void }) {
  const { t } = useTranslation();
  const entries = useQueue({ includeDone: true, batchId, enabled: batchId != null });
  const batches = useBatches();
  const batch = (batches.data ?? []).find((b) => b.id === batchId);
  return (
    <Sheet open={batchId != null} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{batch?.name ?? t("queueSched.batches.details")}</SheetTitle>
          <SheetDescription>{t("queueSched.batches.entries")}</SheetDescription>
        </SheetHeader>
        <div className="space-y-2 px-4 pb-4">
          {batch ? <BatchProgressBar batch={batch} /> : null}
          {(entries.data ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("queueSched.batches.noEntries")}</p>
          ) : (
            <ul className="divide-y divide-border">
              {(entries.data ?? []).map((e) => (
                <li key={e.id} className="flex items-center justify-between gap-2 py-2 text-sm">
                  <span className="truncate">{e.file_name ?? e.model?.name ?? `#${e.id}`}</span>
                  <QueueStateChip state={e.state ?? "waiting"} lastError={e.last_error} />
                </li>
              ))}
            </ul>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

export function BatchesPanel({ openBatchId = null }: { openBatchId?: number | null }) {
  const { t } = useTranslation();
  const { can } = useAuth();
  const batches = useBatches();
  const dispatch = useDispatchBatch();
  const ungroup = useUngroupBatch();
  const del = useDeleteBatch();
  const [dialog, setDialog] = useState<{ batch: Batch | null } | null>(null);
  const [detail, setDetail] = useState<number | null>(openBatchId);
  const [confirm, setConfirm] = useState<{ kind: "delete" | "ungroup"; batch: Batch } | null>(null);
  const canReview = can("queue:review");

  useEffect(() => setDetail(openBatchId), [openBatchId]);

  function err(e: unknown) {
    toast.error(e instanceof ApiError ? e.detail : t("queueSched.batches.requestFailed"));
  }

  if (batches.isLoading) return <Skeleton className="h-48 w-full rounded-lg" />;
  const rows = batches.data ?? [];

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">{t("queueSched.batches.title")}</h2>
        {canReview ? (
          <Button type="button" size="sm" onClick={() => setDialog({ batch: null })}>
            {t("queueSched.batches.create")}
          </Button>
        ) : null}
      </div>
      {rows.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">{t("queueSched.batches.empty")}</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("queueSched.batches.cols.name")}</TableHead>
              <TableHead>{t("queueSched.batches.cols.source")}</TableHead>
              <TableHead>{t("queueSched.batches.cols.qty")}</TableHead>
              <TableHead>{t("queueSched.batches.cols.progress")}</TableHead>
              <TableHead>{t("queueSched.batches.cols.due")}</TableHead>
              <TableHead>{t("queueSched.batches.cols.status")}</TableHead>
              <TableHead>{t("queueSched.batches.cols.actions")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((b) => (
              <TableRow key={b.id}>
                <TableCell>
                  <button type="button" className="font-medium hover:underline" onClick={() => setDetail(b.id)}>
                    {b.name}
                  </button>
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {b.model_name ??
                    b.file_name ??
                    (b.model_id != null
                      ? t("queueSched.batches.modelId", { id: b.model_id })
                      : b.file_id != null
                        ? t("queueSched.batches.fileId", { id: b.file_id })
                        : "-")}
                </TableCell>
                <TableCell>{b.quantity}</TableCell>
                <TableCell>
                  <BatchProgressBar batch={b} />
                  <span className="text-xs text-muted-foreground">
                    {b.progress.done}/{b.quantity}
                  </span>
                </TableCell>
                <TableCell>{b.due_date ?? "-"}</TableCell>
                <TableCell>
                  <Badge variant="outline">{t(`queueSched.batches.status.${b.status}`)}</Badge>
                </TableCell>
                <TableCell>
                  {canReview ? (
                    <div className="flex flex-wrap gap-1">
                      {b.status === "draft" ? (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={dispatch.isPending}
                          onClick={() =>
                            dispatch.mutate(b.id, { onSuccess: () => toast.success(t("queueSched.batches.dispatched")), onError: err })
                          }
                        >
                          {t("queueSched.batches.dispatch")}
                        </Button>
                      ) : null}
                      {b.status === "active" ? (
                        <Button type="button" size="sm" variant="outline" onClick={() => setConfirm({ kind: "ungroup", batch: b })}>
                          {t("queueSched.batches.ungroup")}
                        </Button>
                      ) : null}
                      <Button type="button" size="sm" variant="ghost" onClick={() => setDialog({ batch: b })}>
                        {t("queueSched.batches.edit")}
                      </Button>
                      <Button type="button" size="sm" variant="ghost" onClick={() => setConfirm({ kind: "delete", batch: b })}>
                        {t("queueSched.batches.delete")}
                      </Button>
                    </div>
                  ) : null}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <BatchDialog open={dialog != null} onOpenChange={(o) => !o && setDialog(null)} batch={dialog?.batch ?? null} />
      <BatchDetailSheet batchId={detail} onClose={() => setDetail(null)} />
      <ConfirmDialog
        open={confirm != null}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={confirm?.kind === "delete" ? t("queueSched.batches.deleteTitle") : t("queueSched.batches.ungroupTitle")}
        description={confirm?.kind === "delete" ? t("queueSched.batches.deleteBody") : t("queueSched.batches.ungroupBody")}
        confirmLabel={confirm?.kind === "delete" ? t("queueSched.batches.delete") : t("queueSched.batches.ungroup")}
        destructive={confirm?.kind === "delete"}
        onConfirm={() => {
          if (!confirm) return;
          const { kind, batch } = confirm;
          setConfirm(null);
          if (kind === "delete") del.mutate(batch.id, { onSuccess: () => toast.success(t("queueSched.batches.deleted")), onError: err });
          else ungroup.mutate(batch.id, { onSuccess: () => toast.success(t("queueSched.batches.ungrouped")), onError: err });
        }}
      />
    </div>
  );
}
