/**
 * "Slice" dialog: pick a recipe (same list `PrintDialog`'s Profile step
 * uses), optionally a plate, and start a slice-only job
 * (`POST /files/{id}/slice`). Progress then follows in a toast
 * (`SliceJobsHost`); nothing is queued or sent to a printer.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { usePipelines, useRunPipeline } from "@/api/pipelines";
import { usePrinters } from "@/api/printers";
import { useBedFit, useRecipes, useSlicerHealth, useStartSlice } from "@/api/slicing";
import { BedFitChip } from "@/components/slice/BedFitChip";
import { PrinterPicker } from "@/components/slice/PrinterPicker";
import { trackSlice } from "@/components/slice/sliceTracker";
import { Button } from "@/components/ui/button";
import { DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { useDebouncedValue } from "@/lib/format";
import { filterByName, sortPresets } from "@/lib/presetList";

export function SliceDialog({
  open,
  onOpenChange,
  file,
  modelSlug,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  file: { id: number; rel_path: string };
  /** Enables the pre-flight bed-fit check once a printer is chosen. */
  modelSlug?: string;
}) {
  const [printerId, setPrinterId] = useState<number | null>(null);
  const { t } = useTranslation();
  const [force, setForce] = useState(false);
  // Set when the server refused with `does_not_fit` though the chip had no
  // verdict; keyed to the selection so changing it clears the flag.
  const [serverRefused, setServerRefused] = useState<string | null>(null);
  const recipes = useRecipes(printerId ?? undefined);
  const printersQ = usePrinters();
  const health = useSlicerHealth();
  const start = useStartSlice();
  const pipelines = usePipelines();
  const runPipeline = useRunPipeline();
  const [mode, setMode] = useState<"recipe" | "pipeline">("recipe");
  const [pipelineId, setPipelineId] = useState<number | null>(null);
  const [recipeId, setRecipeId] = useState<number | null>(null);
  const [plate, setPlate] = useState("");
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 250);

  const allRecipes = useMemo(() => (Array.isArray(recipes.data) ? recipes.data : []), [recipes.data]);
  // The chosen printer's own recipes float to the top; search narrows by name.
  const list = useMemo(() => filterByName(sortPresets(allRecipes), debouncedSearch), [allRecipes, debouncedSearch]);
  const printerOptions = useMemo(
    () => (Array.isArray(printersQ.data) ? printersQ.data : []).map((p) => ({ id: p.id, name: p.name })),
    [printersQ.data],
  );
  const pickPrinter = useCallback((id: number) => setPrinterId(id), []);

  // Default the recipe from the chosen printer: its first compatible recipe.
  useEffect(() => {
    if (printerId == null || recipeId != null) return;
    const first = sortPresets(allRecipes).find((r) => r.compatible === true);
    if (first) setRecipeId(first.id);
  }, [printerId, recipeId, allRecipes]);
  const pipelineList = Array.isArray(pipelines.data) ? pipelines.data : [];
  const pipelineMode = mode === "pipeline" && pipelineList.length > 0;
  const fitRecipeId = pipelineMode
    ? (pipelineList.find((p) => p.id === pipelineId)?.recipe_id ?? null)
    : recipeId;
  const bedFit = useBedFit(modelSlug, file.id, { printerId, recipeId: fitRecipeId });
  const selectionKey = `${pipelineMode ? "p" : "r"}${pipelineMode ? pipelineId : recipeId}`;
  const tooLarge = bedFit.data?.status === "too_large" || serverRefused === selectionKey;
  const blocked = tooLarge && !force;
  const plateN = plate.trim() === "" ? null : Number(plate);
  const plateValid = plateN === null || (Number.isInteger(plateN) && plateN >= 1);
  const slicerDown = health.data != null && health.data.ok !== true;
  const name = file.rel_path.split("/").pop() ?? file.rel_path;

  function submitPipeline() {
    if (pipelineId == null || !plateValid) return;
    runPipeline.mutate(
      { id: pipelineId, fileId: file.id, plate: plateN, force: force && tooLarge },
      {
        onSuccess: (run) => {
          if (run.kind === "slice" && run.slice_job) {
            trackSlice({ id: run.slice_job.id, fileName: name });
          } else {
            toast.success(`Queued ${name}; it slices, then prints`);
          }
          onOpenChange(false);
        },
        onError: (e) => {
          if (e instanceof ApiError && e.code === "does_not_fit") setServerRefused(selectionKey);
          toast.error(e instanceof ApiError ? e.detail : "Could not run the pipeline");
        },
      },
    );
  }

  function submit() {
    if (pipelineMode) return submitPipeline();
    if (recipeId == null || !plateValid) return;
    start.mutate(
      { fileId: file.id, recipeId, plate: plateN, force: force && tooLarge },
      {
        onSuccess: (job) => {
          trackSlice({ id: job.id, fileName: name });
          onOpenChange(false);
        },
        onError: (e) => {
          if (e instanceof ApiError && e.code === "does_not_fit") setServerRefused(selectionKey);
          toast.error(e instanceof ApiError ? e.detail : "Could not start the slice");
        },
      },
    );
  }

  return (
    <FormDialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) {
          setMode("recipe");
          setPipelineId(null);
          setRecipeId(null);
          setPlate("");
          setSearch("");
          setPrinterId(null);
          setForce(false);
          setServerRefused(null);
        }
      }}
      dirty={mode !== "recipe" || pipelineId !== null || recipeId !== null || plate !== "" || printerId !== null}
      busy={start.isPending || runPipeline.isPending}
    >
      <DialogHeader>
        <DialogTitle>Slice {name}</DialogTitle>
        <DialogDescription>
          Slices with the chosen recipe and stores the result next to the source file. Nothing is sent to a printer
          (unless you run a pipeline with a queue step).
        </DialogDescription>
      </DialogHeader>
      {pipelineList.length > 0 ? (
        <div className="flex gap-1">
          <Button
            type="button"
            size="sm"
            variant={pipelineMode ? "ghost" : "secondary"}
            aria-pressed={!pipelineMode}
            onClick={() => setMode("recipe")}
          >
            Recipe
          </Button>
          <Button
            type="button"
            size="sm"
            variant={pipelineMode ? "secondary" : "ghost"}
            aria-pressed={pipelineMode}
            onClick={() => setMode("pipeline")}
          >
            Pipeline
          </Button>
        </div>
      ) : null}
      {pipelineMode ? (
        <RadioGroup
          value={pipelineId != null ? String(pipelineId) : ""}
          onValueChange={(v) => setPipelineId(Number(v))}
        >
          {pipelineList.map((p) => (
            <div key={p.id} className="flex items-start gap-2">
              <RadioGroupItem value={String(p.id)} id={`slice-pipeline-${p.id}`} className="mt-0.5" />
              <Label htmlFor={`slice-pipeline-${p.id}`} className="flex flex-col items-start gap-0 font-normal">
                <span>{p.name}</span>
                <span className="text-xs text-muted-foreground">
                  {p.recipe_name ?? "recipe"}
                  {p.steps.length > 0 ? ` · ${p.steps.map((s) => s.kind).join(" → ")}` : ""}
                </span>
              </Label>
            </div>
          ))}
        </RadioGroup>
      ) : recipes.isLoading && allRecipes.length === 0 ? (
        <p className="text-sm text-muted-foreground">Loading recipes…</p>
      ) : allRecipes.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No recipes yet.{" "}
          <Link to="/inventory/profiles" className="underline">
            Create a recipe to slice
          </Link>
          .
        </p>
      ) : (
        <div className="grid gap-2">
          <Input
            type="search"
            aria-label="Search recipes"
            placeholder="Search recipes"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {list.length === 0 ? (
            <p className="text-sm text-muted-foreground">No recipes match "{debouncedSearch.trim()}".</p>
          ) : (
            <RadioGroup value={recipeId != null ? String(recipeId) : ""} onValueChange={(v) => setRecipeId(Number(v))}>
              {list.map((r) => (
                <div key={r.id} className="flex items-center gap-2">
                  <RadioGroupItem value={String(r.id)} id={`slice-recipe-${r.id}`} disabled={r.compatible === false} />
                  <Label htmlFor={`slice-recipe-${r.id}`} className="font-normal">
                    {r.name}
                    {r.compatible === false && r.incompatible_reason ? (
                      <span className="ml-1 text-xs text-muted-foreground">({r.incompatible_reason})</span>
                    ) : null}
                  </Label>
                </div>
              ))}
            </RadioGroup>
          )}
        </div>
      )}
      {printerOptions.length > 0 ? (
        <div className="grid gap-1.5">
          <Label>Printer (optional, prefers its recipes)</Label>
          <PrinterPicker printers={printerOptions} value={printerId} onChange={pickPrinter} idPrefix="slice-printer" />
        </div>
      ) : null}
      <BedFitChip fit={bedFit.data} />
      {tooLarge ? (
        <div className="flex items-center gap-2">
          <input id="slice-force" type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
          <Label htmlFor="slice-force" className="text-xs font-normal">
            {t("bedFit.slicePart")}
          </Label>
        </div>
      ) : null}
      <div className="grid gap-1.5">
        <Label htmlFor="slice-plate">Plate (optional)</Label>
        <Input
          id="slice-plate"
          type="number"
          min={1}
          placeholder="First plate"
          value={plate}
          onChange={(e) => setPlate(e.target.value)}
          className="w-32"
        />
      </div>
      {slicerDown ? (
        <p role="alert" className="text-sm text-destructive">
          The slicer sidecar is not reachable
          {health.data?.error ? `: ${health.data.error}` : ""}.
        </p>
      ) : null}
      <DialogFooter showCloseButton>
        <Button
          type="button"
          disabled={
            (pipelineMode ? pipelineId == null : recipeId == null) ||
            !plateValid ||
            start.isPending ||
            runPipeline.isPending ||
            slicerDown ||
            blocked
          }
          onClick={submit}
        >
          {pipelineMode ? "Run pipeline" : "Slice"}
        </Button>
      </DialogFooter>
    </FormDialog>
  );
}
