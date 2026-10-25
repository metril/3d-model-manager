/**
 * "Slice" dialog: pick a recipe (same list `PrintDialog`'s Profile step
 * uses), optionally a plate, and start a slice-only job
 * (`POST /files/{id}/slice`). Progress then follows in a toast
 * (`SliceJobsHost`); nothing is queued or sent to a printer.
 */
import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useRecipes, useSlicerHealth, useStartSlice } from "@/api/slicing";
import { trackSlice } from "@/components/slice/sliceTracker";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";

export function SliceDialog({
  open,
  onOpenChange,
  file,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  file: { id: number; rel_path: string };
}) {
  const recipes = useRecipes();
  const health = useSlicerHealth();
  const start = useStartSlice();
  const [recipeId, setRecipeId] = useState<number | null>(null);
  const [plate, setPlate] = useState("");

  const list = Array.isArray(recipes.data) ? recipes.data : [];
  const plateN = plate.trim() === "" ? null : Number(plate);
  const plateValid = plateN === null || (Number.isInteger(plateN) && plateN >= 1);
  const slicerDown = health.data != null && health.data.ok !== true;
  const name = file.rel_path.split("/").pop() ?? file.rel_path;

  function submit() {
    if (recipeId == null || !plateValid) return;
    start.mutate(
      { fileId: file.id, recipeId, plate: plateN },
      {
        onSuccess: (job) => {
          trackSlice({ id: job.id, fileName: name });
          onOpenChange(false);
        },
        onError: (e) => toast.error(e instanceof ApiError ? e.detail : "Could not start the slice"),
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Slice {name}</DialogTitle>
          <DialogDescription>
            Slices with the chosen recipe and stores the result next to the source file. Nothing is sent to a printer.
          </DialogDescription>
        </DialogHeader>
        {recipes.isLoading ? (
          <p className="text-sm text-muted-foreground">Loading recipes…</p>
        ) : list.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No recipes yet.{" "}
            <Link to="/inventory/profiles" className="underline">
              Create a recipe to slice
            </Link>
            .
          </p>
        ) : (
          <RadioGroup value={recipeId != null ? String(recipeId) : ""} onValueChange={(v) => setRecipeId(Number(v))}>
            {list.map((r) => (
              <div key={r.id} className="flex items-center gap-2">
                <RadioGroupItem value={String(r.id)} id={`slice-recipe-${r.id}`} />
                <Label htmlFor={`slice-recipe-${r.id}`} className="font-normal">
                  {r.name}
                </Label>
              </div>
            ))}
          </RadioGroup>
        )}
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
            The slicer sidecar is not reachable{health.data?.error ? `: ${health.data.error}` : ""}.
          </p>
        ) : null}
        <DialogFooter showCloseButton>
          <Button type="button" disabled={recipeId == null || !plateValid || start.isPending || slicerDown} onClick={submit}>
            Slice
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
