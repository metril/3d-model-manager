/**
 * The one-modal print flow (P5 Task 11): replaces `SendToPrinterDialog`
 * everywhere it was launched from. Five steps in a left rail -- Source,
 * Plate, Profile, Filament, Send -- fed by a single live `POST /print/plan`
 * (re-fetched on every plate/printer/override change) and finished by
 * `POST /print/send` (or `useEnqueueModel` for "Add to queue", which only
 * touches the model's existing per-model queue -- no plate/printer/mapping
 * is persisted there until P6 adds recipes).
 *
 * `source` decides what the dialog already knows:
 * - `file`: a specific sliced file -- Source step skipped entirely.
 * - `model`: a model with one or more sliced files on its current revision
 *   -- Source is skipped when there's exactly one, else it's a plain file
 *   picker (no model search needed, the model's already chosen).
 * - `archive`: relaunches an archived run via `POST /archives/{id}/
 *   reprint` for the INITIAL plan (seeds plate/printer/overrides from the
 *   archived mapping), then behaves like `file` for anything after.
 * - `pick`: nothing known yet -- Source is a model search, then that
 *   model's sliced files.
 *
 * On first landing (and only once, so it never fights a manual rail
 * click), the dialog skips forward past any step that has nothing to
 * decide: Source when it doesn't apply, Plate when the file has only one
 * plate, and Profile always (P6 adds recipes -- today it's just "As
 * sliced"). Every step stays reachable from the rail regardless.
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ImageIcon } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/api/client";
import { useEnqueueModel } from "@/api/queue";
import { useModelSearchQuery } from "@/api/library";
import { usePrintPlan, useReprint, useSendPrint } from "@/api/printFlow";
import { usePrinterStatus } from "@/api/printers";
import type { PlanFilament, PlanFilamentStatus, RevisionDetail, RevisionSummary } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FilamentChip } from "@/components/ui/filament-chip";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { SpecRow } from "@/components/ui/spec-row";
import { cn } from "@/lib/utils";
import { formatCost, humanizeDuration } from "@/lib/format";

export type PrintSource =
  | { kind: "file"; fileId: number }
  | { kind: "model"; modelId: number }
  | { kind: "archive"; archiveId: number }
  | { kind: "pick" };

const STEPS = ["Source", "Plate", "Profile", "Filament", "Send"] as const;
const SOURCE = 0;
const PLATE = 1;
const PROFILE = 2;
const FILAMENT = 3;
const SEND = 4;

// Mirrors `app.services.spool_inventory.EXTERNAL_UNIT` -- the "external
// spool" sentinel AMS unit, used to label a tray chip "External" instead
// of "AMS 255·254".
const EXTERNAL_UNIT = 255;

const STATUS_STYLE: Record<PlanFilamentStatus, string> = {
  ok: "border-border",
  color: "border-amber-500/50 bg-amber-500/10",
  type: "border-destructive/50 bg-destructive/10",
  none: "border-destructive/50 bg-destructive/10",
};

/** The model's sliced (`gcode_3mf`) files on its current revision -- used
 * by the `model`/`pick` sources' file picker. Piggybacks on the same
 * `["revisions", ...]` query keys `library.ts` uses, so it shares cache. */
function useModelSlicedFiles(modelId: number | null) {
  const revisions = useQuery({
    queryKey: ["revisions", modelId ?? undefined] as const,
    queryFn: () => api.get<RevisionSummary[]>(`/models/${modelId}/revisions`),
    enabled: modelId != null,
  });
  const revisionId = revisions.data?.[0]?.id ?? null;
  const detail = useQuery({
    queryKey: ["revisions", "detail", revisionId] as const,
    queryFn: () => api.get<RevisionDetail>(`/revisions/${revisionId}`),
    enabled: revisionId != null,
  });
  const files = useMemo(() => (detail.data?.files ?? []).filter((f) => f.format === "gcode_3mf"), [detail.data]);
  return { files, isLoading: revisions.isLoading || detail.isLoading };
}

function Rail({ step, onStep }: { step: number; onStep: (i: number) => void }) {
  return (
    <ol className="flex w-40 shrink-0 flex-col gap-0.5 border-r border-border pr-3">
      {STEPS.map((label, i) => (
        <li key={label}>
          <button
            type="button"
            onClick={() => onStep(i)}
            aria-current={i === step ? "step" : undefined}
            aria-label={label}
            className={cn(
              "flex w-full items-center gap-2 rounded-[6px] px-2 py-1.5 text-left text-sm",
              i === step
                ? "border-l-2 border-primary bg-accent/40 font-medium text-foreground"
                : "border-l-2 border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            <span className="label-caps w-4 shrink-0 font-mono">{i + 1}</span>
            {label}
          </button>
        </li>
      ))}
    </ol>
  );
}

function FilamentRow({
  filament,
  trays,
  onOverride,
}: {
  filament: PlanFilament;
  trays: { slot: number; label: string }[];
  onOverride: (filamentId: number, tray: number) => void;
}) {
  return (
    <div
      className={cn("flex flex-wrap items-center gap-3 rounded-[6px] border p-2", STATUS_STYLE[filament.status])}
      data-status={filament.status}
    >
      <FilamentChip color={filament.color ?? undefined} material={filament.type ?? "Filament"} />
      <span className="font-mono text-xs tabular-nums text-muted-foreground">
        {filament.grams != null ? `${Math.round(filament.grams)} g` : "— g"}
      </span>
      {filament.spool ? (
        <span className="text-xs text-muted-foreground">
          {filament.spool.color_name ?? filament.spool.brand ?? "Spool"} #{filament.spool.id}
        </span>
      ) : null}
      {filament.status !== "ok" ? (
        <span className="text-xs text-destructive">
          {filament.status === "none" ? "No tray assigned" : filament.status === "type" ? "Type mismatch" : null}
        </span>
      ) : null}
      <div className="ml-auto">
        <Select
          value={filament.tray != null ? String(filament.tray) : ""}
          onValueChange={(v) => onOverride(filament.filament_id, Number(v))}
          aria-label={`Tray for ${filament.type ?? "filament"}`}
        >
          <SelectTrigger className="h-8 w-36">
            <SelectValue placeholder="Choose tray" />
          </SelectTrigger>
          <SelectContent>
            {trays.map((t) => (
              <SelectItem key={t.slot} value={String(t.slot)}>
                {t.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </div>
  );
}

export function PrintDialog({
  open,
  onOpenChange,
  source,
  onSent,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  source: PrintSource;
  onSent?: () => void;
}) {
  const [step, setStep] = useState(0);
  const [autoAdvanced, setAutoAdvanced] = useState(false);
  const [pickedModelId, setPickedModelId] = useState<number | null>(null);
  const [pickedFileId, setPickedFileId] = useState<number | null>(null);
  const [plate, setPlate] = useState<number | null>(null);
  const [printerId, setPrinterId] = useState<number | null>(null);
  const [overrides, setOverrides] = useState<Record<number, number>>({});
  const [bedLevelling, setBedLevelling] = useState(true);
  const [flowCali, setFlowCali] = useState(true);
  const [timelapse, setTimelapse] = useState(false);
  const [optionsSeeded, setOptionsSeeded] = useState(false);
  const [modelQuery, setModelQuery] = useState("");

  // Reset everything when the dialog is relaunched for a different source
  // (or reopened) -- a stale plate/printer/override set from a previous
  // launch must not leak into this one.
  useEffect(() => {
    if (!open) return;
    setStep(source.kind === "pick" ? SOURCE : source.kind === "model" ? SOURCE : PLATE);
    setAutoAdvanced(false);
    setPickedModelId(null);
    setPickedFileId(null);
    setPlate(null);
    setPrinterId(null);
    setOverrides({});
    setOptionsSeeded(false);
    setModelQuery("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, source.kind, source.kind === "file" ? source.fileId : null, source.kind === "model" ? source.modelId : null, source.kind === "archive" ? source.archiveId : null]);

  const modelIdForFiles = source.kind === "model" ? source.modelId : pickedModelId;
  const slicedFiles = useModelSlicedFiles(modelIdForFiles);
  const autoFileId = slicedFiles.files.length === 1 ? slicedFiles.files[0].id : null;

  const resolvedFileId =
    source.kind === "file" ? source.fileId : source.kind !== "archive" ? pickedFileId ?? autoFileId : null;

  const [archiveFileId, setArchiveFileId] = useState<number | null>(null);
  const reprint = useReprint(source.kind === "archive" ? source.archiveId : null);

  useEffect(() => {
    if (source.kind === "archive" && reprint.data && archiveFileId == null) {
      setArchiveFileId(reprint.data.file_id);
      setPlate(reprint.data.plate);
      setPrinterId(reprint.data.printer_id);
      const seeded: Record<number, number> = {};
      for (const f of reprint.data.filaments) if (f.tray != null) seeded[f.filament_id] = f.tray;
      setOverrides(seeded);
    }
  }, [source, reprint.data, archiveFileId]);

  const effectiveFileId = source.kind === "archive" ? archiveFileId : resolvedFileId;
  const usingReprintPlan = source.kind === "archive" && archiveFileId == null;

  // `plate`/`printerId` state only holds an EXPLICIT choice -- omitted from
  // the plan body (left `undefined`) until the person actually picks one,
  // so the plan's own defaults never trigger a second fetch under it (the
  // plan it already returned already reflects its own defaults).
  const planQuery = usePrintPlan(
    !usingReprintPlan && effectiveFileId != null
      ? { file_id: effectiveFileId, plate: plate ?? undefined, printer_id: printerId ?? undefined, overrides }
      : null,
  );
  const plan = usingReprintPlan ? reprint.data : planQuery.data;
  const planError = usingReprintPlan ? reprint.error : planQuery.error;
  const effectivePlate = plate ?? plan?.plate ?? null;
  const effectivePrinterId = printerId ?? plan?.printer_id ?? null;

  // Seed the toggle defaults from the plan's own defaults exactly once, so
  // later refetches (an override, a plate change) never clobber what the
  // person already chose.
  useEffect(() => {
    if (!plan || optionsSeeded) return;
    setBedLevelling(plan.options.bed_levelling);
    setFlowCali(plan.options.flow_cali);
    setTimelapse(plan.options.timelapse);
    setOptionsSeeded(true);
  }, [plan, optionsSeeded]);

  // Auto-advance past any step with nothing to decide, exactly once.
  useEffect(() => {
    if (!plan || autoAdvanced) return;
    let next = step;
    const sourceNeeded = source.kind === "pick" || (source.kind === "model" && slicedFiles.files.length > 1);
    if (next === SOURCE && !sourceNeeded) next = PLATE;
    if (next === PLATE && plan.plates.length <= 1) next = PROFILE;
    if (next === PROFILE) next = FILAMENT;
    setStep(next);
    setAutoAdvanced(true);
  }, [plan, autoAdvanced, step, source, slicedFiles.files.length]);

  const enqueueModel = useEnqueueModel();
  const sendPrint = useSendPrint();
  const printerStatus = usePrinterStatus(effectivePrinterId ?? -1, { enabled: effectivePrinterId != null });
  const trays = (printerStatus.data?.trays ?? []).map((t) => ({
    slot: t.slot,
    label: t.ams_unit === EXTERNAL_UNIT ? "External" : `AMS ${t.ams_unit}·${t.tray}`,
  }));

  const modelSearch = useModelSearchQuery(modelQuery);
  const searchResults = modelQuery.trim().length > 0 ? modelSearch.data?.items ?? [] : [];

  const hasRed = (plan?.filaments ?? []).some((f) => f.status === "none" || f.status === "type");
  // A reprint seeds `printerId` from the archived run before the person
  // ever visits the Send step -- that pre-selected printer can be busy or
  // incompatible by now, and the radio group only disables an OPTION, it
  // never clears a stale selection. Send must stay disabled until the
  // selected printer is actually idle + compatible.
  const selectedPrinter = plan?.printers.find((pp) => pp.id === effectivePrinterId) ?? null;
  const printerNotReady = selectedPrinter != null && !(selectedPrinter.idle && selectedPrinter.compatible);
  const modelId = plan?.model_id ?? (source.kind === "model" ? source.modelId : null);

  function close() {
    onOpenChange(false);
  }

  function handleSend() {
    if (!plan || effectivePrinterId == null || effectiveFileId == null) return;
    sendPrint.mutate(
      {
        file_id: effectiveFileId,
        plate: plan.plate ?? undefined,
        printer_id: effectivePrinterId,
        use_ams: plan.use_ams,
        ams_mapping: plan.ams_mapping,
        bed_levelling: bedLevelling,
        flow_cali: flowCali,
        timelapse,
      },
      {
        onSuccess: () => {
          close();
          toast.success("Sent to printer");
          onSent?.();
        },
      },
    );
  }

  function handleAddToQueue() {
    if (modelId == null) return;
    enqueueModel.mutate(modelId, {
      onSuccess: () => {
        close();
        toast.success("Added to queue");
      },
    });
  }

  const footerPlate = plan?.plates.find((p) => p.index === effectivePlate) ?? plan?.plates[0];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] max-w-3xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="border-b border-border px-5 py-3">
          <DialogTitle>Print</DialogTitle>
        </DialogHeader>
        <div className="flex min-h-0 flex-1 gap-4 overflow-y-auto p-5">
          <Rail step={step} onStep={setStep} />
          <div className="min-w-0 flex-1 space-y-4">
            {planError ? (
              <p role="alert" className="text-sm text-destructive">
                {planError instanceof Error ? planError.message : "Couldn't build a print plan."}
              </p>
            ) : null}

            {step === SOURCE ? (
              <div className="space-y-3">
                {source.kind === "pick" && pickedModelId == null ? (
                  <div className="space-y-2">
                    <Label htmlFor="print-dialog-model-search">Find a model</Label>
                    <Input
                      id="print-dialog-model-search"
                      value={modelQuery}
                      onChange={(e) => setModelQuery(e.target.value)}
                      placeholder="Search models…"
                    />
                    <ul className="divide-y divide-border">
                      {searchResults.map((m) => (
                        <li key={m.id}>
                          <button
                            type="button"
                            className="w-full py-2 text-left text-sm hover:underline"
                            onClick={() => setPickedModelId(m.id)}
                          >
                            {m.name}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : (
                  <ul className="divide-y divide-border">
                    {slicedFiles.files.map((f) => (
                      <li key={f.id}>
                        <button
                          type="button"
                          className="w-full py-2 text-left text-sm hover:underline"
                          onClick={() => setPickedFileId(f.id)}
                        >
                          {f.rel_path}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ) : null}

            {step === PLATE ? (
              <div className="flex gap-3 overflow-x-auto pb-2">
                {(plan?.plates ?? []).map((p) => (
                  <button
                    type="button"
                    key={p.index}
                    onClick={() => setPlate(p.index)}
                    className={cn(
                      "w-40 shrink-0 space-y-2 rounded-lg border p-3 text-left",
                      effectivePlate === p.index ? "border-primary" : "border-border",
                    )}
                  >
                    <div className="flex aspect-square items-center justify-center overflow-hidden rounded bg-muted">
                      {p.thumbnail_available && effectiveFileId != null ? (
                        <img
                          src={`/api/blobs/${plan?.file_id}/plates/${p.index}/thumb`}
                          alt={`Plate ${p.index}`}
                          className="h-full w-full object-cover"
                        />
                      ) : (
                        <ImageIcon className="size-8 text-muted-foreground" />
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Plate {p.index}
                      {p.weight_g != null ? ` · ${Math.round(p.weight_g)} g` : ""}
                    </p>
                  </button>
                ))}
              </div>
            ) : null}

            {step === PROFILE ? (
              <RadioGroup value="as-sliced">
                <div className="flex items-center gap-2">
                  <RadioGroupItem value="as-sliced" id="print-dialog-profile-as-sliced" />
                  <Label htmlFor="print-dialog-profile-as-sliced" className="font-normal">
                    As sliced
                  </Label>
                </div>
              </RadioGroup>
            ) : null}

            {step === FILAMENT ? (
              <div className="space-y-2">
                {(plan?.filaments ?? []).map((f) => (
                  <FilamentRow
                    key={f.filament_id}
                    filament={f}
                    trays={trays}
                    onOverride={(filamentId, tray) =>
                      setOverrides((prev) => ({ ...prev, [filamentId]: tray }))
                    }
                  />
                ))}
                {(plan?.filaments ?? []).length === 0 ? (
                  <p className="text-sm text-muted-foreground">No filament needs on this plate.</p>
                ) : null}
              </div>
            ) : null}

            {step === SEND ? (
              <div className="space-y-4">
                <RadioGroup
                  value={effectivePrinterId != null ? String(effectivePrinterId) : ""}
                  onValueChange={(v) => setPrinterId(Number(v))}
                >
                  {(plan?.printers ?? []).map((p) => {
                    const disabled = !(p.idle && p.compatible);
                    return (
                      <div key={p.id} className="flex items-center gap-2">
                        <RadioGroupItem value={String(p.id)} id={`print-dialog-printer-${p.id}`} disabled={disabled} />
                        <Label htmlFor={`print-dialog-printer-${p.id}`} className="font-normal">
                          {p.name}
                          {disabled && p.reason ? (
                            <span className="ml-1 text-xs text-muted-foreground">({p.reason})</span>
                          ) : null}
                        </Label>
                      </div>
                    );
                  })}
                </RadioGroup>
                <div className="space-y-2">
                  <label className="flex items-center gap-2 text-sm">
                    <Checkbox checked={bedLevelling} onCheckedChange={(v) => setBedLevelling(v === true)} />
                    Bed levelling
                  </label>
                  <label className="flex items-center gap-2 text-sm">
                    <Checkbox checked={flowCali} onCheckedChange={(v) => setFlowCali(v === true)} />
                    Flow calibration
                  </label>
                  <label className="flex items-center gap-2 text-sm">
                    <Checkbox checked={timelapse} onCheckedChange={(v) => setTimelapse(v === true)} />
                    Timelapse
                  </label>
                </div>
                {(plan?.warnings ?? []).length > 0 ? (
                  <ul className="space-y-1">
                    {plan!.warnings.map((w, i) => (
                      <li key={i} className="text-sm text-amber-600">
                        {w}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {sendPrint.isError ? (
                  <p role="alert" className="text-sm text-destructive">
                    Could not send to printer
                  </p>
                ) : null}
                <div className="flex gap-2">
                  <Button
                    type="button"
                    onClick={handleSend}
                    disabled={effectivePrinterId == null || hasRed || printerNotReady || sendPrint.isPending}
                  >
                    {sendPrint.isPending ? "Sending..." : "Send now"}
                  </Button>
                  {modelId != null ? (
                    <Button type="button" variant="outline" onClick={handleAddToQueue} disabled={enqueueModel.isPending}>
                      Add to queue
                    </Button>
                  ) : null}
                </div>
              </div>
            ) : null}
          </div>
        </div>
        <div className="flex items-center justify-between border-t border-border px-5 py-3">
          <SpecRow
            items={[
              footerPlate?.prediction_s != null ? { label: humanizeDuration(footerPlate.prediction_s) } : null,
              footerPlate?.weight_g != null ? { label: `${Math.round(footerPlate.weight_g)} g` } : null,
              plan?.est_cost != null ? { label: formatCost(plan.est_cost) } : null,
            ]}
          />
          <div className="flex gap-2">
            <Button type="button" variant="outline" disabled={step === SOURCE} onClick={() => setStep((s) => Math.max(SOURCE, s - 1))}>
              Back
            </Button>
            {step < SEND ? (
              <Button type="button" onClick={() => setStep((s) => Math.min(SEND, s + 1))}>
                Next
              </Button>
            ) : null}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
