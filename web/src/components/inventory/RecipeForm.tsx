/**
 * Create/edit dialog for a print recipe (P6 Task 9): a named bundle of
 * printer/process/filament profiles + a bed type + dispatch options that
 * `QueuePage`/`PrintDialog` can hand to the slicer/dispatcher in one pick.
 * Mirrors `SpoolForm.tsx`'s controlled-dialog shape.
 *
 * Picking a printer profile filters the process/filament selects to that
 * profile's `printer_model` (profiles with no `printer_model` are treated
 * as universal). The bed type select's options come from the printer
 * profile's own `data.curr_bed_type` list (Bambu Studio's printer preset
 * field) -- falling back to the single value "Textured PEI Plate" when a
 * profile doesn't carry one. `n` only applies (and is required >=1) for
 * fanout "first_n".
 */
import { useTranslation } from "react-i18next";
import { useEffect, useState, type FormEvent } from "react";

import { ApiError } from "@/api/client";
import { usePrinters, usePrinterStatus } from "@/api/printers";
import { useProfiles, useSaveRecipe } from "@/api/slicing";
import type { Fanout, PlanOptions, PrintRecipe, PrintRecipeIn, SlicerProfile } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DialogClose,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { DEFAULT_NOZZLE, modelKey, scopeMismatchReason } from "@/lib/profileScope";

const FALLBACK_BED_TYPES = ["Textured PEI Plate"];
const FILAMENT_SLOTS = 4;
const FANOUTS: Fanout[] = ["single", "spread", "first_n"];

function bedTypesFor(printerProfile: SlicerProfile | undefined): string[] {
  const raw = printerProfile?.data?.curr_bed_type;
  if (Array.isArray(raw) && raw.length > 0) return raw.map(String);
  if (typeof raw === "string" && raw.trim()) return [raw];
  return FALLBACK_BED_TYPES;
}


interface ScopedOption {
  profile: SlicerProfile;
  /** Out-of-scope reason, null when the profile fits. */
  reason: string | null;
}

/** Compatible profiles first; out-of-scope ones only with "show all" -- or
 * when already selected, so a stale pick stays visible (with a warning). */
function scopedOptions(
  profiles: SlicerProfile[] | undefined,
  key: string | null,
  nozzle: number | null,
  showAll: boolean,
  selectedIds: (number | null)[],
): { options: ScopedOption[]; selectedOutOfScope: boolean } {
  const all = (profiles ?? []).map((profile) => ({ profile, reason: scopeMismatchReason(profile, key, nozzle) }));
  const options = [
    ...all.filter((o) => o.reason === null),
    ...all.filter((o) => o.reason !== null && (showAll || selectedIds.includes(o.profile.id))),
  ];
  return {
    options,
    selectedOutOfScope: all.some((o) => o.reason !== null && selectedIds.includes(o.profile.id)),
  };
}

function optionLabel(o: ScopedOption): string {
  return o.reason ? `${o.profile.name} (${o.reason})` : o.profile.name;
}

function fieldsFor(recipe: PrintRecipe | undefined) {
  const slots = recipe?.filament_profile_ids ?? [];
  const filaments = Array.from({ length: FILAMENT_SLOTS }, (_, i) => slots[i] ?? null);
  return {
    name: recipe?.name ?? "",
    printerProfileId: recipe?.printer_profile_id ?? null,
    processProfileId: recipe?.process_profile_id ?? null,
    filamentProfileIds: filaments,
    bedType: recipe?.bed_type ?? "",
    options: {
      bed_levelling: (recipe?.options?.bed_levelling as boolean | undefined) ?? true,
      flow_cali: (recipe?.options?.flow_cali as boolean | undefined) ?? true,
      timelapse: (recipe?.options?.timelapse as boolean | undefined) ?? false,
    } satisfies PlanOptions,
    targetPrinterId: recipe?.target_printer_id ?? null,
    targetPrinterModel: recipe?.target_printer_model ?? "",
    fanout: recipe?.fanout ?? ("single" as Fanout),
    n: recipe?.n != null ? String(recipe.n) : "",
  };
}

export function RecipeForm({
  open,
  onOpenChange,
  recipe,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  recipe?: PrintRecipe;
  onSaved?: (recipe: PrintRecipe) => void;
}) {
  const { t } = useTranslation();
  const isEdit = recipe !== undefined;
  const printerProfiles = useProfiles("printer");
  const processProfiles = useProfiles("process");
  const filamentProfiles = useProfiles("filament");
  const printers = usePrinters();
  const saveRecipe = useSaveRecipe();

  const [fields, setFields] = useState(() => fieldsFor(recipe));
  const [baseline, setBaseline] = useState(() => JSON.stringify(fieldsFor(recipe)));
  const [touchedN, setTouchedN] = useState(false);

  useEffect(() => {
    if (open) {
      setFields(fieldsFor(recipe));
      setBaseline(JSON.stringify(fieldsFor(recipe)));
      setTouchedN(false);
      setShowAll(false);
      saveRecipe.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const dirty = JSON.stringify(fields) !== baseline;

  const selectedPrinterProfile = (printerProfiles.data ?? []).find(
    (p) => p.id === fields.printerProfileId,
  );
  // Scope: the target printer when set (nozzle = live status -> last seen ->
  // 0.4), else the picked printer profile's own model/nozzle.
  const targetPrinter = (printers.data ?? []).find((p) => p.id === fields.targetPrinterId);
  const targetStatus = usePrinterStatus(targetPrinter?.id ?? 0, { enabled: targetPrinter != null });
  const scopeKey = targetPrinter
    ? modelKey(targetPrinter.model)
    : (selectedPrinterProfile?.printer_model_key ?? modelKey(selectedPrinterProfile?.printer_model));
  const scopeNozzle = targetPrinter
    ? (targetStatus.data?.nozzle_mm ?? targetPrinter.nozzle_mm ?? DEFAULT_NOZZLE)
    : (selectedPrinterProfile?.nozzle_mm ?? null);
  const [showAll, setShowAll] = useState(false);
  const scopedPrinter = scopedOptions(printerProfiles.data, targetPrinter ? scopeKey : null, scopeNozzle, showAll, [
    fields.printerProfileId,
  ]);
  const scopedProcess = scopedOptions(processProfiles.data, scopeKey, scopeNozzle, showAll, [
    fields.processProfileId,
  ]);
  const scopedFilament = scopedOptions(filamentProfiles.data, scopeKey, scopeNozzle, showAll, fields.filamentProfileIds);
  const staleSelection = [scopedPrinter, scopedProcess, scopedFilament].some((o) => o.selectedOutOfScope);
  const bedTypes = bedTypesFor(selectedPrinterProfile);

  const nRequired = fields.fanout === "first_n";
  const nValue = fields.n.trim() === "" ? null : Number(fields.n);
  const nInvalid = nRequired && (nValue == null || !Number.isInteger(nValue) || nValue < 1);

  function setFilamentSlot(index: number, value: number | null) {
    setFields((f) => {
      const next = [...f.filamentProfileIds];
      next[index] = value;
      return { ...f, filamentProfileIds: next };
    });
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (fields.printerProfileId == null || fields.processProfileId == null) return;
    if (nInvalid) {
      setTouchedN(true);
      return;
    }
    const payload: PrintRecipeIn = {
      name: fields.name.trim(),
      printer_profile_id: fields.printerProfileId,
      process_profile_id: fields.processProfileId,
      filament_profile_ids: fields.filamentProfileIds,
      bed_type: fields.bedType || bedTypes[0] || FALLBACK_BED_TYPES[0],
      options: fields.options,
      target_printer_id: fields.targetPrinterId,
      target_printer_model: fields.targetPrinterModel.trim() || null,
      fanout: fields.fanout,
      n: nRequired ? nValue : null,
    };
    saveRecipe.mutate(
      { id: recipe?.id, payload },
      { onSuccess: (r) => { onSaved?.(r); onOpenChange(false); } },
    );
  }

  return (
    <FormDialog open={open} onOpenChange={onOpenChange} dirty={dirty} busy={saveRecipe.isPending}>
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>{isEdit ? t("recipeForm.editTitle") : t("recipeForm.newTitle")}</DialogTitle>
            <DialogDescription>
              {t("recipeForm.desc")}
            </DialogDescription>
          </DialogHeader>

          <div className="flex max-h-[65vh] flex-col gap-4 overflow-y-auto py-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="recipe-name">{t("recipeForm.name")}</Label>
              <Input
                id="recipe-name"
                value={fields.name}
                onChange={(e) => setFields((f) => ({ ...f, name: e.target.value }))}
                required
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="recipe-printer-profile">{t("recipeForm.printerProfile")}</Label>
              <Select
                aria-label={t("recipeForm.printerProfile")}
                value={fields.printerProfileId != null ? String(fields.printerProfileId) : ""}
                onValueChange={(v) =>
                  setFields((f) => ({ ...f, printerProfileId: Number(v), processProfileId: null, filamentProfileIds: Array(FILAMENT_SLOTS).fill(null) }))
                }
              >
                <SelectTrigger id="recipe-printer-profile" aria-label={t("recipeForm.printerProfile")} className="w-full">
                  <SelectValue placeholder={t("recipeForm.chooseAPrinterProfile")} />
                </SelectTrigger>
                <SelectContent>
                  {scopedPrinter.options.map((o) => (
                    <SelectItem key={o.profile.id} value={String(o.profile.id)}>
                      {optionLabel(o)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="recipe-process-profile">{t("recipeForm.processProfile")}</Label>
              <Select
                aria-label={t("recipeForm.processProfile")}
                value={fields.processProfileId != null ? String(fields.processProfileId) : ""}
                onValueChange={(v) => setFields((f) => ({ ...f, processProfileId: Number(v) }))}
                disabled={fields.printerProfileId == null}
              >
                <SelectTrigger id="recipe-process-profile" aria-label={t("recipeForm.processProfile")} className="w-full">
                  <SelectValue placeholder={t("recipeForm.chooseAProcessProfile")} />
                </SelectTrigger>
                <SelectContent>
                  {scopedProcess.options.map((o) => (
                    <SelectItem key={o.profile.id} value={String(o.profile.id)}>
                      {optionLabel(o)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-col gap-2">
              <Label>{t("recipeForm.filamentsAmsSlots")}</Label>
              <div className="grid grid-cols-2 gap-2">
                {fields.filamentProfileIds.map((value, index) => (
                  <Select
                    key={index}
                    aria-label={t("recipeForm.filamentSlot", { n: index + 1 })}
                    value={value != null ? String(value) : "none"}
                    onValueChange={(v) => setFilamentSlot(index, v === "none" ? null : Number(v))}
                    disabled={fields.printerProfileId == null}
                  >
                    <SelectTrigger aria-label={t("recipeForm.filamentSlot", { n: index + 1 })} className="w-full">
                      <SelectValue placeholder={t("recipeForm.slot", { n: index + 1 })} />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">{t("recipeForm.empty")}</SelectItem>
                      {scopedFilament.options.map((o) => (
                        <SelectItem key={o.profile.id} value={String(o.profile.id)}>
                          {optionLabel(o)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ))}
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="recipe-bed-type">{t("recipeForm.bedType")}</Label>
              <Select
                aria-label={t("recipeForm.bedType")}
                value={fields.bedType || bedTypes[0] || ""}
                onValueChange={(v) => setFields((f) => ({ ...f, bedType: v }))}
              >
                <SelectTrigger id="recipe-bed-type" aria-label={t("recipeForm.bedType")} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {bedTypes.map((bt) => (
                    <SelectItem key={bt} value={bt}>
                      {bt}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="flex items-center gap-2 text-sm">
                <Checkbox checked={showAll} onCheckedChange={(c) => setShowAll(c === true)} />
                {t("recipeForm.showAllProfiles")}
              </label>
              <p className="text-xs text-muted-foreground">
                {t("recipeForm.scopeNote", {
                  who: targetPrinter ? t("recipeForm.scopeTarget") : t("recipeForm.scopeChosen"),
                  nozzle: scopeNozzle != null ? ` (${scopeNozzle} mm)` : "",
                })}
              </p>
              {staleSelection ? (
                <p role="status" className="text-xs text-amber-600 dark:text-amber-400">
                  {t("recipeForm.staleSelection")}
                </p>
              ) : null}
            </div>

            <div className="flex flex-col gap-2">
              <Label>{t("recipeForm.options")}</Label>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={fields.options.bed_levelling}
                  onCheckedChange={(c) => setFields((f) => ({ ...f, options: { ...f.options, bed_levelling: c === true } }))}
                />
                {t("recipeForm.bedLevelling")}
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={fields.options.flow_cali}
                  onCheckedChange={(c) => setFields((f) => ({ ...f, options: { ...f.options, flow_cali: c === true } }))}
                />
                {t("recipeForm.flowCalibration")}
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={fields.options.timelapse}
                  onCheckedChange={(c) => setFields((f) => ({ ...f, options: { ...f.options, timelapse: c === true } }))}
                />
                {t("recipeForm.timelapse")}
              </label>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="recipe-target-printer">{t("recipeForm.targetPrinterOptional")}</Label>
                <Select
                  aria-label={t("recipeForm.targetPrinter")}
                  value={fields.targetPrinterId != null ? String(fields.targetPrinterId) : "any"}
                  onValueChange={(v) => setFields((f) => ({ ...f, targetPrinterId: v === "any" ? null : Number(v) }))}
                >
                  <SelectTrigger id="recipe-target-printer" aria-label={t("recipeForm.targetPrinter")} className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="any">{t("recipeForm.anyPrinter")}</SelectItem>
                    {(printers.data ?? []).map((p) => (
                      <SelectItem key={p.id} value={String(p.id)}>
                        {p.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="recipe-target-model">{t("recipeForm.targetModelOptional")}</Label>
                <Input
                  id="recipe-target-model"
                  placeholder={t("recipeForm.eGA1Mini")}
                  value={fields.targetPrinterModel}
                  onChange={(e) => setFields((f) => ({ ...f, targetPrinterModel: e.target.value }))}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="recipe-fanout">{t("recipeForm.fanout")}</Label>
                <Select aria-label={t("recipeForm.fanout")} value={fields.fanout} onValueChange={(v) => setFields((f) => ({ ...f, fanout: v as Fanout }))}>
                  <SelectTrigger id="recipe-fanout" aria-label={t("recipeForm.fanout")} className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {FANOUTS.map((fo) => (
                      <SelectItem key={fo} value={fo}>
                        {t(`recipeForm.fanoutOpt.${fo}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {nRequired ? (
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="recipe-n">N</Label>
                  <Input
                    id="recipe-n"
                    type="number"
                    min={1}
                    value={fields.n}
                    onChange={(e) => setFields((f) => ({ ...f, n: e.target.value }))}
                    onBlur={() => setTouchedN(true)}
                    aria-invalid={touchedN && nInvalid}
                  />
                  {touchedN && nInvalid ? (
                    <p role="alert" className="text-xs text-destructive">
                      {t("recipeForm.nInvalid")}
                    </p>
                  ) : null}
                </div>
              ) : null}
            </div>

            {saveRecipe.isError ? (
              <p role="alert" className="text-sm text-destructive">
                {saveRecipe.error instanceof ApiError ? saveRecipe.error.detail : t("recipeForm.couldNotSaveThisRecipe")}
              </p>
            ) : null}
          </div>

          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="outline">{t("common.cancel")}</Button>
            </DialogClose>
            <Button type="submit" disabled={saveRecipe.isPending || fields.printerProfileId == null || fields.processProfileId == null}>
              {saveRecipe.isPending ? t("recipeForm.saving") : isEdit ? t("recipeForm.saveChanges") : t("recipeForm.create")}
            </Button>
          </DialogFooter>
        </form>
    </FormDialog>
  );
}
