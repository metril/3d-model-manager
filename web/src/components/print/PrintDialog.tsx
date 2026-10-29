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
 * plate, and Profile always (its default, "As sliced", needs no further
 * input -- a recipe is still reached via a manual rail click). Every step
 * stays reachable from the rail regardless.
 *
 * P6 Task 11 adds the Profile step's other option: a recipe (filtered to
 * ones whose printer profile matches the plan's printer, or all when none
 * is chosen yet). Picking one swaps the Send step's "Send now" for "Slice
 * and send" (enqueues at the front, state `waiting` -- the dispatcher
 * slices then sends) / "Add to queue" (appends, state `ready`), both via
 * `useEnqueue`, and the Plate/Filament steps -- which need an already-
 * sliced file -- collapse to "Mapped after slicing" instead of calling
 * `/print/plan`.
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ImageIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { useAuth } from "@/api/auth";
import { api, ApiError } from "@/api/client";
import { useCostCenters, useFinanceConfig } from "@/api/finance";
import { useCreateBatch, useDispatchBatch, useEnqueue } from "@/api/queue";
import { useFeatures } from "@/api/features";
import { useModelSearchQuery, useRecentModelsQuery } from "@/api/library";
import { needsReview } from "@/lib/permissions";
import { usePrintPlan, useReprint, useSendPrint } from "@/api/printFlow";
import { usePrinterStatus, usePrinters } from "@/api/printers";
import { useRecipes, useSlicerHealth } from "@/api/slicing";
import type { BlobFormat, PlanFilament, QueueEnqueueBody, PlanFilamentStatus, RevisionDetail, RevisionSummary } from "@/api/types";
import { SchedulingSection } from "@/components/print/SchedulingSection";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";
import { FilamentChip } from "@/components/ui/filament-chip";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { SpecRow } from "@/components/ui/spec-row";
import { draftToFields, EMPTY_SCHEDULE, isScheduleSet, NO_BATCH, type BatchChoice, type ScheduleDraft } from "@/lib/schedule";
import { cn } from "@/lib/utils";
import { formatCost, humanizeDuration } from "@/lib/format";

export type PrintSource =
  | { kind: "file"; fileId: number; format?: string }
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
  color: "border-warning/50 bg-warning/10",
  type: "border-destructive/50 bg-destructive/10",
  none: "border-destructive/50 bg-destructive/10",
};

// "As sliced" picks from the model's already-sliced files; choosing a
// recipe picks from its sliceable sources instead (the recipe does its
// own slicing).
const AS_SLICED_FORMATS: BlobFormat[] = ["gcode_3mf"];
const RECIPE_SOURCE_FORMATS: BlobFormat[] = ["3mf", "stl", "step", "obj", "iges"];

/** The model's files matching `formats` on its current revision -- used by
 * the `model`/`pick` sources' file picker. Piggybacks on the same
 * `["revisions", ...]` query keys `library.ts` uses, so it shares cache. */
function useModelSourceFiles(modelId: number | null, formats: BlobFormat[]) {
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
  const files = useMemo(
    () => (detail.data?.files ?? []).filter((f) => formats.includes(f.format)),
    [detail.data, formats],
  );
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
  printerId: printerIdProp,
  sliceOnly: sliceOnlyProp = false,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  source: PrintSource;
  onSent?: () => void;
  /** Seeds the printer selection (e.g. launched from a printer card). */
  printerId?: number;
  /** The chosen file is an unsliced source: start in recipe mode. */
  sliceOnly?: boolean;
}) {
  // A file source's format: passed by the caller, else fetched.
  const passedFormat = source.kind === "file" ? source.format : undefined;
  const fileQuery = useQuery({
    queryKey: ["files", "format", source.kind === "file" ? source.fileId : null] as const,
    queryFn: () => api.get<{ format: string }>(`/files/${source.kind === "file" ? source.fileId : 0}`),
    enabled: open && source.kind === "file" && passedFormat == null,
  });
  const fileFormat = passedFormat ?? fileQuery.data?.format;
  const [step, setStep] = useState(0);
  const [autoAdvanced, setAutoAdvanced] = useState(false);
  const [pickedModelId, setPickedModelId] = useState<number | null>(null);
  const [pickedFileId, setPickedFileId] = useState<number | null>(null);
  const [plate, setPlate] = useState<number | null>(null);
  const [printerId, setPrinterId] = useState<number | null>(printerIdProp ?? null);
  const [sliceOnly, setSliceOnly] = useState(sliceOnlyProp);
  const [overrides, setOverrides] = useState<Record<number, number>>({});
  const [bedLevelling, setBedLevelling] = useState(true);
  const [flowCali, setFlowCali] = useState(true);
  const [timelapse, setTimelapse] = useState(false);
  const [optionsSeeded, setOptionsSeeded] = useState(false);
  const [modelQuery, setModelQuery] = useState("");
  const [profileMode, setProfileMode] = useState<"as-sliced" | "recipe">("as-sliced");
  const [selectedRecipeId, setSelectedRecipeId] = useState<number | null>(null);
  const [copies, setCopies] = useState(1);
  const [costCenterId, setCostCenterId] = useState<number | null>(null);
  const [costCenterSeeded, setCostCenterSeeded] = useState(false);
  // T4: collapsed-by-default scheduling section; untouched = payload unchanged.
  const [schedOpen, setSchedOpen] = useState(false);
  const [sched, setSched] = useState<ScheduleDraft>(EMPTY_SCHEDULE);
  const [batchChoice, setBatchChoice] = useState<BatchChoice>(NO_BATCH);
  // Set by direct user choices (plate/printer/options/...); auto-seeded defaults do not count.
  const [touched, setTouched] = useState(false);

  // Reset everything when the dialog is relaunched for a different source
  // (or reopened) -- a stale plate/printer/override set from a previous
  // launch must not leak into this one.
  useEffect(() => {
    if (!open) return;
    setStep(source.kind === "pick" ? SOURCE : source.kind === "model" ? SOURCE : sliceOnlyProp ? PROFILE : PLATE);
    setAutoAdvanced(false);
    setPickedModelId(null);
    setPickedFileId(null);
    setPlate(null);
    setPrinterId(printerIdProp ?? null);
    setSliceOnly(sliceOnlyProp);
    setOverrides({});
    setOptionsSeeded(false);
    setModelQuery("");
    setProfileMode(sliceOnlyProp ? "recipe" : "as-sliced");
    setSelectedRecipeId(null);
    setCopies(1);
    setCostCenterId(null);
    setCostCenterSeeded(false);
    setSchedOpen(false);
    setSched(EMPTY_SCHEDULE);
    setBatchChoice(NO_BATCH);
    setTouched(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, printerIdProp, sliceOnlyProp, source.kind, source.kind === "file" ? source.fileId : null, source.kind === "model" ? source.modelId : null, source.kind === "archive" ? source.archiveId : null]);

  // Once a file source's format is known it decides the mode: sliced ->
  // as-sliced (never a recipe), anything else -> recipe. Runs after the
  // reset effect above.
  useEffect(() => {
    if (!open || source.kind !== "file" || fileFormat == null) return;
    const sliced = fileFormat === "gcode_3mf";
    setSliceOnly(!sliced);
    setProfileMode(sliced ? "as-sliced" : "recipe");
    setSelectedRecipeId(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, source.kind, source.kind === "file" ? source.fileId : null, fileFormat]);

  const modelIdForFiles = source.kind === "model" ? source.modelId : pickedModelId;
  // Picking a different model (pick mode) starts the mode decision over.
  useEffect(() => {
    if (source.kind !== "pick") return;
    setSliceOnly(sliceOnlyProp);
    setProfileMode("as-sliced");
    setSelectedRecipeId(null);
    setPickedFileId(null);
  }, [pickedModelId, source.kind, sliceOnlyProp]);

  const features = useFeatures();
  const allPrintersQ = usePrinters();
  const canPrinter = useAuth().canPrinter;
  const slicerConfigured = !!features.data?.slicer_configured;
  const gcodeFiles = useModelSourceFiles(modelIdForFiles, AS_SLICED_FORMATS);
  const sourceFiles = useModelSourceFiles(modelIdForFiles, RECIPE_SOURCE_FORMATS);
  const slicedFiles = profileMode === "recipe" ? sourceFiles : gcodeFiles;
  // A model with only unsliced sources (and a slicer to slice them) goes
  // straight to recipe mode instead of an empty "no files" list.
  useEffect(() => {
    if (source.kind === "file" || source.kind === "archive" || modelIdForFiles == null) return;
    if (gcodeFiles.isLoading || sourceFiles.isLoading || sliceOnly) return;
    if (slicerConfigured && gcodeFiles.files.length === 0 && sourceFiles.files.length > 0) {
      setSliceOnly(true);
      setProfileMode("recipe");
    }
  }, [source.kind, modelIdForFiles, gcodeFiles, sourceFiles, slicerConfigured, sliceOnly]);
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
    !usingReprintPlan && effectiveFileId != null && profileMode !== "recipe" && !(source.kind === "file" && (fileFormat == null || fileFormat !== "gcode_3mf"))
      ? { file_id: effectiveFileId, plate: plate ?? undefined, printer_id: printerId ?? undefined, overrides }
      : null,
  );
  const plan = usingReprintPlan ? reprint.data : profileMode === "recipe" ? undefined : planQuery.data;
  const planError = usingReprintPlan ? reprint.error : planQuery.error;
  // Recipe mode has no plan to default the printer from: use the only
  // printer the person can print on, if there is exactly one.
  const soleRecipePrinterId = (() => {
    if (profileMode !== "recipe" || !Array.isArray(allPrintersQ.data)) return null;
    const usable = allPrintersQ.data.filter((p) => canPrinter?.(p.id, "print") ?? true);
    return usable.length === 1 ? usable[0].id : null;
  })();
  const effectivePlate = plate ?? plan?.plate ?? null;
  const effectivePrinterId = printerId ?? plan?.printer_id ?? soleRecipePrinterId;

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
  // Recipe mode (an unsliced file) has no plan: it stops at Profile, where
  // the recipe is chosen.
  const recipeReady = profileMode === "recipe" && effectiveFileId != null;
  useEffect(() => {
    if ((!plan && !recipeReady) || autoAdvanced) return;
    let next = step;
    const sourceNeeded = source.kind === "pick" || (source.kind === "model" && slicedFiles.files.length > 1);
    if (next === SOURCE && !sourceNeeded) next = PLATE;
    if (next === PLATE && (recipeReady || (plan?.plates.length ?? 0) <= 1)) next = PROFILE;
    if (next === PROFILE && !recipeReady) next = FILAMENT;
    setStep(next);
    setAutoAdvanced(true);
  }, [plan, recipeReady, autoAdvanced, step, source, slicedFiles.files.length]);

  const { t } = useTranslation();
  const enqueueRaw = useEnqueue();
  const createBatch = useCreateBatch();
  const dispatchBatch = useDispatchBatch();
  const enqueue = {
    isPending: enqueueRaw.isPending || createBatch.isPending || dispatchBatch.isPending,
  };
  const schedActive = isScheduleSet(sched) || batchChoice.mode !== null;

  /** Every queue submit goes through here so the T4 scheduling fields (and the
   * "new batch" path: create + dispatch instead of a bare enqueue) apply. */
  function submitQueue(body: QueueEnqueueBody, onSuccess: () => void) {
    const fields = draftToFields(sched);
    if (batchChoice.mode === "new") {
      const name =
        batchChoice.name.trim() || t("queueSched.defaultBatchName", { date: new Date().toLocaleDateString() });
      createBatch
        .mutateAsync({
          name,
          quantity: batchChoice.quantity,
          file_id: body.file_id ?? null,
          recipe_id: body.recipe_id ?? null,
          printer_id: body.printer_id ?? null,
          plate_targets: body.plate != null ? { [String(body.plate)]: batchChoice.quantity } : {},
          cost_center_id: body.cost_center_id ?? null,
          ams_mapping: body.ams_mapping ?? null,
          options: body.options ?? null,
          ...(body.copies != null ? { copies_per_entry: body.copies } : {}),
          ...fields,
        })
        .then((b) => {
          // Created: a failed dispatch must not let a retry create a 2nd batch.
          setBatchChoice({ ...batchChoice, mode: b.id });
          return dispatchBatch.mutateAsync(b.id).then(onSuccess, () => {
            toast.error(t("queueSched.batchDraftOnly"));
          });
        })
        .catch(() => undefined);
      return;
    }
    const withBatch = typeof batchChoice.mode === "number" ? { ...fields, batch_id: batchChoice.mode } : fields;
    enqueueRaw.mutate({ ...body, ...withBatch }, { onSuccess });
  }
  const sendPrint = useSendPrint();
  const auth = useAuth();
  const financeConfig = useFinanceConfig();
  const billingOn = !!financeConfig.data?.billing_enabled;
  const costCenters = useCostCenters({ mine: true });
  // Default to the person's own ("Personal") center -- their private
  // center always exists in `?mine=true` (lazily created server-side) and
  // is the only one with no `owner_user_id`/`group_id` distinction worth
  // showing specially; it's simply the one owned by them.
  useEffect(() => {
    if (!billingOn || costCenterSeeded || costCenterId != null) return;
    const centers = costCenters.data ?? [];
    if (centers.length === 0) return;
    const personal = centers.find((c) => c.owner_user_id === auth.data?.id) ?? centers[0];
    setCostCenterId(personal.id);
    setCostCenterSeeded(true);
  }, [billingOn, costCenterSeeded, costCenterId, costCenters.data, auth.data?.id]);
  const printerStatus = usePrinterStatus(effectivePrinterId ?? -1, { enabled: effectivePrinterId != null });
  const trays = (printerStatus.data?.trays ?? []).map((t) => ({
    slot: t.slot,
    label: t.ams_unit === EXTERNAL_UNIT ? "External" : `AMS ${t.ams_unit}·${t.tray}`,
  }));

  // Recipe mode has no plan to list printers from, so offer every printer
  // the person may print on.
  const printerList = useMemo(() => (Array.isArray(allPrintersQ.data) ? allPrintersQ.data : []), [allPrintersQ.data]);
  const printerName = printerIdProp != null ? printerList.find((p) => p.id === printerIdProp)?.name : undefined;
  const planPrinters = useMemo(
    () =>
      profileMode === "recipe"
        ? printerList
            .filter((p) => canPrinter?.(p.id, "print") ?? true)
            .map((p) => ({ id: p.id, name: p.name, model: p.model ?? null, idle: true, compatible: true, reason: null as string | null }))
        : plan?.printers ?? [],
    [profileMode, printerList, plan?.printers, canPrinter],
  );

  const recent = useRecentModelsQuery(source.kind === "pick" && modelQuery.trim().length === 0);
  const modelSearch = useModelSearchQuery(modelQuery);
  const searchResults = modelQuery.trim().length > 0 ? modelSearch.data?.items ?? [] : [];

  // Profile step: every recipe, annotated by the backend (the single source
  // of truth) as compatible with the chosen printer or not; incompatible
  // ones render disabled with the reason.
  const recipes = useRecipes(effectivePrinterId ?? undefined);
  const slicerHealth = useSlicerHealth();
  const selectedPrinterModel = planPrinters.find((p) => p.id === effectivePrinterId)?.model ?? null;
  const recipeList = Array.isArray(recipes.data) ? recipes.data : [];
  const allRecipesIncompatible = recipeList.length > 0 && recipeList.every((r) => r.compatible === false);
  const selectedRecipe = recipeList.find((r) => r.id === selectedRecipeId) ?? null;
  const recipeActionsDisabled = slicerHealth.data?.ok !== true;
  // The printer changed under a chosen recipe that no longer fits it.
  useEffect(() => {
    if (selectedRecipe?.compatible === false) setSelectedRecipeId(null);
  }, [selectedRecipe]);

  // A file known to be an already-sliced gcode_3mf is always "as sliced".
  const effectiveIsSliced =
    source.kind === "file"
      ? fileFormat === "gcode_3mf"
      : effectiveFileId != null && gcodeFiles.files.some((f) => f.id === effectiveFileId);

  const formatUnknown = source.kind === "file" && fileFormat == null;

  const hasRed = (plan?.filaments ?? []).some((f) => f.status === "none" || f.status === "type");
  // A reprint seeds `printerId` from the archived run before the person
  // ever visits the Send step -- that pre-selected printer can be busy or
  // incompatible by now, and the radio group only disables an OPTION, it
  // never clears a stale selection. Send must stay disabled until the
  // selected printer is actually idle + compatible.
  const selectedPrinter = planPrinters.find((pp) => pp.id === effectivePrinterId) ?? null;
  const selectedPrinterOut = (allPrintersQ.data ?? []).find((pp) => pp.id === effectivePrinterId) ?? null;
  const printerNotReady = selectedPrinter != null && !(selectedPrinter.idle && selectedPrinter.compatible);

  // P7 review/billing plan Task 9: once review or billing applies, the
  // server answers `POST /print/send` with 409 `queue_required` instead of
  // actually sending -- "Send now" becomes "Send next" (front-of-queue
  // enqueue) so the person isn't stuck on a button that always 409s. This
  // is a best-effort client-side hint (`needsReview`); `handleSend` falls
  // back to the same enqueue path if the server disagrees anyway.
  const sendNext = billingOn || needsReview(auth.data, effectivePrinterId);

  const dirty =
    touched ||
    copies !== 1 ||
    modelQuery !== "" ||
    selectedRecipeId != null ||
    batchChoice !== NO_BATCH ||
    JSON.stringify(sched) !== JSON.stringify(EMPTY_SCHEDULE);
  const submitting = sendPrint.isPending || enqueue.isPending;

  function close() {
    onOpenChange(false);
  }

  function handleSendNext() {
    if (effectiveFileId == null) return;
    submitQueue(
      {
        file_id: effectiveFileId,
        plate: effectivePlate ?? undefined,
        printer_id: effectivePrinterId ?? undefined,
        ams_mapping: plan?.ams_mapping ?? undefined,
        options: { bed_levelling: bedLevelling, flow_cali: flowCali, timelapse },
        front: true,
        cost_center_id: billingOn ? costCenterId : undefined,
      },
      () => {
        close();
        toast.success(
          needsReview(auth.data, effectivePrinterId) ? "Sent for review" : "Queued next",
        );
        onSent?.();
      },
    );
  }

  function handleSend() {
    if (!plan || effectivePrinterId == null || effectiveFileId == null) return;
    if (sendNext || schedActive) {
      handleSendNext();
      return;
    }
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
        onError: (e) => {
          // The client's `sendNext` guess can be wrong (e.g. a group's
          // `requires_review` flag this user's own `Me` doesn't expose) --
          // the server's 409 `queue_required` is authoritative for THAT
          // specific case, so fall back to the same enqueue path rather
          // than leaving the person stuck. Any OTHER 409 (printer busy,
          // disabled, etc.) is a real failure -- surface its own detail
          // below instead of silently enqueueing.
          if (e instanceof ApiError && e.status === 409 && e.detail === "queue_required") {
            handleSendNext();
          }
        },
      },
    );
  }

  // "As sliced" Add to queue: persists the plan's own plate/printer/mapping
  // so the dispatcher can send it unchanged later (state `ready`).
  function handleAddToQueue() {
    if (effectiveFileId == null) return;
    submitQueue(
      {
        file_id: effectiveFileId,
        plate: effectivePlate ?? undefined,
        printer_id: effectivePrinterId ?? undefined,
        ams_mapping: plan?.ams_mapping ?? undefined,
        options: { bed_levelling: bedLevelling, flow_cali: flowCali, timelapse },
      },
      () => {
        close();
        toast.success("Added to queue");
      },
    );
  }

  // Recipe "Slice and send": inserted at the front (`front: true`, state
  // `waiting`) -- the dispatcher slices with the recipe then sends.
  function handleSliceAndSend() {
    if (effectiveFileId == null || selectedRecipeId == null) return;
    submitQueue(
      {
        file_id: effectiveFileId,
        recipe_id: selectedRecipeId,
        printer_id: effectivePrinterId ?? undefined,
        copies,
        front: true,
      },
      () => {
        close();
        toast.success(`Slicing, then sending to ${selectedPrinter?.name ?? "printer"}`);
      },
    );
  }

  // Recipe "Add to queue": appended at the end (`front: false`).
  function handleQueueRecipe() {
    if (effectiveFileId == null || selectedRecipeId == null) return;
    submitQueue(
      {
        file_id: effectiveFileId,
        recipe_id: selectedRecipeId,
        printer_id: effectivePrinterId ?? undefined,
        copies,
        front: false,
      },
      () => {
        close();
        toast.success("Added to queue");
      },
    );
  }

  const footerPlate = plan?.plates.find((p) => p.index === effectivePlate) ?? plan?.plates[0];

  return (
    <FormDialog open={open} onOpenChange={onOpenChange} dirty={dirty} busy={submitting} className="flex max-h-[85vh] sm:max-w-3xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="border-b border-border px-5 py-3">
          <DialogTitle>{printerIdProp != null ? `Print on ${printerName ?? "printer"}` : "Print"}</DialogTitle>
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
                    <p className="text-xs text-muted-foreground">
                      {modelQuery.trim().length === 0
                        ? "Pick a model to print, or search by name. "
                        : ""}
                      {slicerConfigured
                        ? "Sliced .gcode.3mf files send directly; other models are sliced with a recipe first."
                        : "Only sliced .gcode.3mf files can be printed (slicing needs the slicer sidecar)."}
                    </p>
                    {modelQuery.trim().length === 0 ? (
                      <p className="label-caps text-muted-foreground">Recent models</p>
                    ) : null}
                    <ul className="divide-y divide-border">
                      {(modelQuery.trim().length === 0 ? recent.data?.items ?? [] : searchResults).map((m) => (
                        <li key={m.id}>
                          <button
                            type="button"
                            className="w-full py-2 text-left text-sm hover:underline"
                            onClick={() => { setTouched(true); setPickedModelId(m.id); }}
                          >
                            {m.name}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : (
                  <>
                    <ul className="divide-y divide-border">
                      {slicedFiles.files.map((f) => (
                        <li key={f.id}>
                          <button
                            type="button"
                            className="w-full py-2 text-left text-sm hover:underline"
                            onClick={() => { setTouched(true); setPickedFileId(f.id); }}
                          >
                            {f.rel_path}
                          </button>
                        </li>
                      ))}
                    </ul>
                    {modelIdForFiles != null &&
                    !gcodeFiles.isLoading &&
                    !sourceFiles.isLoading &&
                    slicedFiles.files.length === 0 ? (
                      <p className="text-sm text-muted-foreground" data-testid="no-printable-files">
                        {profileMode === "recipe"
                          ? "This model has no sliceable files (3mf, stl, step, obj)."
                          : slicerConfigured
                            ? "This model has no sliced .gcode.3mf and no sliceable file (3mf, stl, step, obj). A recipe is needed to slice."
                            : "This model has no sliced .gcode.3mf file. Only .gcode.3mf can be printed; slicing 3mf/stl/step/obj needs the slicer sidecar (TDMM_SLICER_URL)."}
                      </p>
                    ) : null}
                  </>
                )}
              </div>
            ) : null}

            {step === PLATE && profileMode === "recipe" ? (
              <p className="text-sm text-muted-foreground">Mapped after slicing</p>
            ) : step === PLATE ? (
              <div className="flex gap-3 overflow-x-auto pb-2">
                {(plan?.plates ?? []).map((p) => (
                  <button
                    type="button"
                    key={p.index}
                    onClick={() => { setTouched(true); setPlate(p.index); }}
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
              <RadioGroup
                value={profileMode === "recipe" && selectedRecipeId != null ? String(selectedRecipeId) : "as-sliced"}
                onValueChange={(v) => {
                  if (v === "as-sliced") {
                    setProfileMode("as-sliced");
                    setSelectedRecipeId(null);
                  } else {
                    setProfileMode("recipe");
                    setSelectedRecipeId(Number(v));
                  }
                }}
              >
                {sliceOnly ? (
                  <p className="text-sm text-muted-foreground">
                    This file isn't sliced yet. Choose a recipe: the slicer sidecar slices it, then it is sent to the printer.
                  </p>
                ) : (
                  <div className="flex items-center gap-2">
                    <RadioGroupItem value="as-sliced" id="print-dialog-profile-as-sliced" />
                    <Label htmlFor="print-dialog-profile-as-sliced" className="font-normal">
                      As sliced
                    </Label>
                  </div>
                )}
                {!effectiveIsSliced && !formatUnknown && recipeList.map((r) => (
                  <div key={r.id} className="flex items-center gap-2">
                    <RadioGroupItem
                      value={String(r.id)}
                      id={`print-dialog-profile-recipe-${r.id}`}
                      disabled={r.compatible === false}
                    />
                    <Label htmlFor={`print-dialog-profile-recipe-${r.id}`} className="font-normal">
                      {r.name}
                    </Label>
                    {r.compatible === false && r.incompatible_reason ? (
                      <span className="text-xs text-muted-foreground">{r.incompatible_reason}</span>
                    ) : null}
                  </div>
                ))}
                {profileMode === "recipe" && !effectiveIsSliced && !formatUnknown && (recipeList.length === 0 || allRecipesIncompatible) && !recipes.isLoading ? (
                  <p className="text-sm text-muted-foreground">
                    {recipeList.length === 0
                      ? "No recipes yet."
                      : `None of your recipes fit ${selectedPrinterModel ?? "this printer"}.`}{" "}
                    <Link to="/inventory/profiles" className="underline">
                      Create a recipe to slice
                    </Link>
                    .
                  </p>
                ) : null}
              </RadioGroup>
            ) : null}

            {step === FILAMENT && profileMode === "recipe" ? (
              <p className="text-sm text-muted-foreground">Mapped after slicing</p>
            ) : step === FILAMENT ? (
              <div className="space-y-2">
                {(plan?.filaments ?? []).map((f) => (
                  <FilamentRow
                    key={f.filament_id}
                    filament={f}
                    trays={trays}
                    onOverride={(filamentId, tray) =>
                      { setTouched(true); setOverrides((prev) => ({ ...prev, [filamentId]: tray })); }
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
                  onValueChange={(v) => { setTouched(true); setPrinterId(Number(v)); }}
                >
                  {planPrinters.map((p) => {
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
                    <Checkbox checked={bedLevelling} onCheckedChange={(v) => { setTouched(true); setBedLevelling(v === true); }} />
                    Bed levelling
                  </label>
                  <label className="flex items-center gap-2 text-sm">
                    <Checkbox checked={flowCali} onCheckedChange={(v) => { setTouched(true); setFlowCali(v === true); }} />
                    Flow calibration
                  </label>
                  <label className="flex items-center gap-2 text-sm">
                    <Checkbox checked={timelapse} onCheckedChange={(v) => { setTouched(true); setTimelapse(v === true); }} />
                    Timelapse
                  </label>
                </div>
                {billingOn ? (
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="print-dialog-cost-center">Cost center</Label>
                    <Select
                      value={costCenterId != null ? String(costCenterId) : ""}
                      onValueChange={(v) => { setTouched(true); setCostCenterId(Number(v)); }}
                      aria-label="Cost center"
                    >
                      <SelectTrigger id="print-dialog-cost-center" className="w-64">
                        <SelectValue placeholder="Choose a cost center" />
                      </SelectTrigger>
                      <SelectContent>
                        {(costCenters.data ?? []).map((c) => (
                          <SelectItem key={c.id} value={String(c.id)}>
                            {c.owner_user_id === auth.data?.id ? "Personal" : c.name}
                            {c.available != null ? ` — ${formatCost(c.available)} available` : ""}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                ) : null}
                <SchedulingSection
                  open={schedOpen}
                  onOpenChange={setSchedOpen}
                  draft={sched}
                  onChange={setSched}
                  batch={batchChoice}
                  onBatchChange={setBatchChoice}
                  printer={selectedPrinterOut}
                />
                {(plan?.warnings ?? []).length > 0 ? (
                  <ul className="space-y-1">
                    {plan!.warnings.map((w, i) => (
                      <li key={i} className="text-sm text-warning">
                        {w}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {sendPrint.isError &&
                !(
                  sendPrint.error instanceof ApiError &&
                  sendPrint.error.status === 409 &&
                  sendPrint.error.detail === "queue_required"
                ) ? (
                  <p role="alert" className="text-sm text-destructive">
                    {/* I3: a non-queue_required failure (printer busy,
                        disabled, etc.) surfaces the server's own detail
                        instead of a generic message. */}
                    {sendPrint.error instanceof ApiError
                      ? sendPrint.error.detail
                      : "Could not send to printer"}
                  </p>
                ) : null}
                {profileMode === "recipe" ? (
                  <div className="flex items-center gap-2">
                    <Label htmlFor="print-dialog-copies" className="font-normal">
                      Copies
                    </Label>
                    <Input
                      id="print-dialog-copies"
                      type="number"
                      min={1}
                      max={50}
                      className="w-20"
                      value={copies}
                      onChange={(e) => setCopies(Math.min(50, Math.max(1, Number(e.target.value) || 1)))}
                    />
                  </div>
                ) : null}
                <div className="flex gap-2">
                  {profileMode === "recipe" ? (
                    <>
                      <Button
                        type="button"
                        onClick={handleSliceAndSend}
                        disabled={recipeActionsDisabled || selectedRecipeId == null || effectiveFileId == null || enqueue.isPending}
                        title={
                          recipeActionsDisabled
                            ? "Slicer sidecar unavailable. Enable it with the compose `slicer` profile (--profile slicer) and set TDMM_SLICER_URL."
                            : selectedRecipeId == null
                              ? "Choose a recipe on the Profile step"
                              : undefined
                        }
                      >
                        Slice and send
                      </Button>
                      {/* M6: "Add to queue" only queues the recipe for the
                          dispatcher to slice later -- it doesn't call the
                          sidecar itself, so a down sidecar shouldn't block it. */}
                      <Button
                        type="button"
                        variant="outline"
                        onClick={handleQueueRecipe}
                        disabled={selectedRecipeId == null || effectiveFileId == null || enqueue.isPending}
                      >
                        Add to queue
                      </Button>
                    </>
                  ) : (
                    <>
                      <div className="flex flex-col gap-1">
                        <Button
                          type="button"
                          onClick={handleSend}
                          disabled={
                            effectivePrinterId == null ||
                            hasRed ||
                            // M4: in "Send next" mode this enqueues rather than
                            // sending straight to the printer -- queueing is
                            // the whole point when the printer isn't ready
                            // yet, so `printerNotReady` only blocks "Send now".
                            (!(sendNext || schedActive) && printerNotReady) ||
                            sendPrint.isPending ||
                            enqueue.isPending
                          }
                        >
                          {sendPrint.isPending || enqueue.isPending
                            ? "Sending..."
                            : sendNext || schedActive
                              ? "Send next"
                              : "Send now"}
                        </Button>
                        {sendNext ? (
                          <span className="text-xs text-muted-foreground">
                            {needsReview(auth.data, effectivePrinterId)
                              ? "Needs review"
                              : `Billed to ${
                                  (costCenters.data ?? []).find((c) => c.id === costCenterId)?.owner_user_id ===
                                  auth.data?.id
                                    ? "Personal"
                                    : (costCenters.data ?? []).find((c) => c.id === costCenterId)?.name ?? "cost center"
                                }`}
                          </span>
                        ) : null}
                      </div>
                      {effectiveFileId != null ? (
                        <Button type="button" variant="outline" onClick={handleAddToQueue} disabled={enqueue.isPending}>
                          Add to queue
                        </Button>
                      ) : null}
                    </>
                  )}
                </div>
              </div>
            ) : null}
          </div>
        </div>
        <div className="flex items-center justify-between border-t border-border px-5 py-3">
          {profileMode === "recipe" ? (
            <p className="text-sm text-muted-foreground">{selectedRecipe?.name ?? "Recipe"}</p>
          ) : (
            <SpecRow
              items={[
                footerPlate?.prediction_s != null ? { label: humanizeDuration(footerPlate.prediction_s) } : null,
                footerPlate?.weight_g != null ? { label: `${Math.round(footerPlate.weight_g)} g` } : null,
                plan?.est_cost != null ? { label: formatCost(plan.est_cost) } : null,
              ]}
            />
          )}
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
    </FormDialog>
  );
}
