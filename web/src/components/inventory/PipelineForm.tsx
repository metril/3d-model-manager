/**
 * Create/edit dialog for a slicer pipeline: a recipe plus ordered steps.
 * Pre-steps (orient, arrange) override the recipe's slice options; the final
 * step is either nothing (keep the sliced file), a rename of it, or queueing
 * the print (rename and queue are exclusive -- the queue names its own slice).
 */
import { useEffect, useState, type FormEvent } from "react";

import { ApiError } from "@/api/client";
import { useSavePipeline, type PipelineStep, type SlicerPipeline } from "@/api/pipelines";
import { usePrinters } from "@/api/printers";
import { useRecipes } from "@/api/slicing";
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

type Final = "none" | "rename" | "queue";

const DEFAULT_TEMPLATE = "{name} - {recipe} p{plate}";

function fieldsFor(p: SlicerPipeline | undefined) {
  const steps = p?.steps ?? [];
  const rename = steps.find((s) => s.kind === "rename");
  const queue = steps.find((s) => s.kind === "queue");
  return {
    name: p?.name ?? "",
    description: p?.description ?? "",
    recipeId: p?.recipe_id ?? null,
    plate: p?.plate != null ? String(p.plate) : "",
    orient: steps.some((s) => s.kind === "orient"),
    arrange: steps.some((s) => s.kind === "arrange"),
    final: (queue ? "queue" : rename ? "rename" : "none") as Final,
    template: rename?.kind === "rename" ? rename.template : DEFAULT_TEMPLATE,
    printerId: queue?.kind === "queue" ? queue.printer_id : null,
    copies: queue?.kind === "queue" ? String(queue.copies) : "1",
    front: queue?.kind === "queue" ? queue.front : false,
    manualStart: queue?.kind === "queue" ? queue.manual_start : false,
  };
}

export function PipelineForm({
  open,
  onOpenChange,
  pipeline,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  pipeline?: SlicerPipeline;
}) {
  const recipes = useRecipes();
  const printers = usePrinters();
  const save = useSavePipeline();
  const [f, setF] = useState(() => fieldsFor(pipeline));
  const [baseline, setBaseline] = useState(() => JSON.stringify(fieldsFor(pipeline)));

  useEffect(() => {
    if (open) {
      setF(fieldsFor(pipeline));
      setBaseline(JSON.stringify(fieldsFor(pipeline)));
      save.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only when (re)opened
  }, [open]);

  const isEdit = pipeline != null;
  const dirty = JSON.stringify(f) !== baseline;
  const plateN = f.plate.trim() === "" ? null : Number(f.plate);
  const plateValid = plateN === null || (Number.isInteger(plateN) && plateN >= 1);
  const copiesN = Number(f.copies);
  const copiesValid = Number.isInteger(copiesN) && copiesN >= 1 && copiesN <= 50;
  const templateValid = f.template.trim() !== "" && !/[\\/]/.test(f.template);
  const valid =
    f.name.trim() !== "" &&
    f.recipeId != null &&
    plateValid &&
    (f.final !== "rename" || templateValid) &&
    (f.final !== "queue" || copiesValid);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!valid || f.recipeId == null) return;
    const steps: PipelineStep[] = [];
    if (f.orient) steps.push({ kind: "orient" });
    if (f.arrange) steps.push({ kind: "arrange" });
    if (f.final === "rename") steps.push({ kind: "rename", template: f.template.trim() });
    if (f.final === "queue")
      steps.push({
        kind: "queue",
        printer_id: f.printerId,
        copies: copiesN,
        front: f.front,
        manual_start: f.manualStart,
      });
    save.mutate(
      {
        id: pipeline?.id,
        payload: {
          name: f.name.trim(),
          description: f.description.trim() || null,
          recipe_id: f.recipeId,
          plate: plateN,
          steps,
        },
      },
      { onSuccess: () => onOpenChange(false) },
    );
  }

  return (
    <FormDialog open={open} onOpenChange={onOpenChange} dirty={dirty} busy={save.isPending}>
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit pipeline" : "New pipeline"}</DialogTitle>
          <DialogDescription>
            Run a recipe with extra steps in one click: tweak the slice first, then rename the result or send it
            to the print queue.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="pipeline-name">Name</Label>
            <Input id="pipeline-name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="pipeline-description">Description (optional)</Label>
            <Input
              id="pipeline-description"
              value={f.description}
              onChange={(e) => setF({ ...f, description: e.target.value })}
            />
          </div>
          <div className="grid grid-cols-[1fr_auto] gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="pipeline-recipe">Slice with recipe</Label>
              <Select
                aria-label="Recipe"
                value={f.recipeId != null ? String(f.recipeId) : ""}
                onValueChange={(v) => setF({ ...f, recipeId: Number(v) })}
              >
                <SelectTrigger id="pipeline-recipe" aria-label="Recipe" className="w-full">
                  <SelectValue placeholder="Choose a recipe" />
                </SelectTrigger>
                <SelectContent>
                  {(Array.isArray(recipes.data) ? recipes.data : []).map((r) => (
                    <SelectItem key={r.id} value={String(r.id)}>
                      {r.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="pipeline-plate">Plate</Label>
              <Input
                id="pipeline-plate"
                type="number"
                min={1}
                placeholder="First"
                className="w-24"
                value={f.plate}
                onChange={(e) => setF({ ...f, plate: e.target.value })}
                aria-invalid={!plateValid}
              />
            </div>
          </div>

          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-sm font-medium">Before slicing</legend>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={f.orient} onCheckedChange={(c) => setF({ ...f, orient: c === true })} />
              Auto-orient the model
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={f.arrange} onCheckedChange={(c) => setF({ ...f, arrange: c === true })} />
              Auto-arrange the plate
            </label>
          </fieldset>

          <div className="flex flex-col gap-2">
            <Label htmlFor="pipeline-final">After slicing</Label>
            <Select aria-label="After slicing" value={f.final} onValueChange={(v) => setF({ ...f, final: v as Final })}>
              <SelectTrigger id="pipeline-final" aria-label="After slicing" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">Keep the sliced file</SelectItem>
                <SelectItem value="rename">Rename the sliced file</SelectItem>
                <SelectItem value="queue">Add to the print queue</SelectItem>
              </SelectContent>
            </Select>

            {f.final === "rename" ? (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="pipeline-template">File name template</Label>
                <Input
                  id="pipeline-template"
                  value={f.template}
                  onChange={(e) => setF({ ...f, template: e.target.value })}
                  aria-invalid={!templateValid}
                />
                <p className="text-xs text-muted-foreground">
                  Placeholders: {"{name}"} (source file), {"{recipe}"}, {"{pipeline}"}, {"{plate}"}. The
                  .gcode.3mf extension is added.
                </p>
              </div>
            ) : null}

            {f.final === "queue" ? (
              <div className="flex flex-col gap-3 rounded-lg border p-3">
                <div className="grid grid-cols-[1fr_auto] gap-3">
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="pipeline-printer">Printer</Label>
                    <Select
                      aria-label="Printer"
                      value={f.printerId != null ? String(f.printerId) : "any"}
                      onValueChange={(v) => setF({ ...f, printerId: v === "any" ? null : Number(v) })}
                    >
                      <SelectTrigger id="pipeline-printer" aria-label="Printer" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="any">Any eligible printer</SelectItem>
                        {(printers.data ?? []).map((p) => (
                          <SelectItem key={p.id} value={String(p.id)}>
                            {p.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="pipeline-copies">Copies</Label>
                    <Input
                      id="pipeline-copies"
                      type="number"
                      min={1}
                      max={50}
                      className="w-20"
                      value={f.copies}
                      onChange={(e) => setF({ ...f, copies: e.target.value })}
                      aria-invalid={!copiesValid}
                    />
                  </div>
                </div>
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox checked={f.front} onCheckedChange={(c) => setF({ ...f, front: c === true })} />
                  Put at the front of the queue
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox
                    checked={f.manualStart}
                    onCheckedChange={(c) => setF({ ...f, manualStart: c === true })}
                  />
                  Wait for a manual start
                </label>
              </div>
            ) : null}
          </div>

          {save.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {save.error instanceof ApiError ? save.error.detail : "Could not save this pipeline"}
            </p>
          ) : null}
          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="outline">Cancel</Button>
            </DialogClose>
            <Button type="submit" disabled={!valid || save.isPending}>
              {save.isPending ? "Saving..." : isEdit ? "Save changes" : "Create pipeline"}
            </Button>
          </DialogFooter>
        </form>
    </FormDialog>
  );
}
