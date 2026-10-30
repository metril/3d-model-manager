/**
 * "Combine onto one plate": copies of the selected mesh files are laid out on
 * a bed (a printer profile's printable area, or a custom size) and stored as
 * one multi-object 3MF on the current revision. The fit preview is the
 * server's own dry-run plan, so it matches what gets written.
 */
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { bedFromProfile, useCombine, useCombinePlan, type CombineRequest } from "@/api/combine";
import { ApiError } from "@/api/client";
import { useProfiles } from "@/api/slicing";
import type { FileOut } from "@/api/types";
import { Button } from "@/components/ui/button";
import { DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const MAX_OBJECTS = 100;
const COLORS = ["#2dd4bf", "#60a5fa", "#f59e0b", "#f472b6", "#a78bfa", "#34d399", "#fb7185", "#fbbf24"];

function baseName(relPath: string): string {
  return relPath.split("/").pop() ?? relPath;
}

export function CombineDialog({
  open,
  onOpenChange,
  slug,
  files,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  slug: string;
  files: FileOut[];
}) {
  const profiles = useProfiles("printer");
  const combine = useCombine(slug);
  const [copies, setCopies] = useState<Record<number, number>>({});
  const [spacing, setSpacing] = useState("5");
  const [layout, setLayout] = useState<"grid" | "auto">("auto");
  const [profileId, setProfileId] = useState<string>("");
  const [bedX, setBedX] = useState("256");
  const [bedY, setBedY] = useState("256");
  const [name, setName] = useState("");

  const withBed = (profiles.data ?? []).map((p) => ({ profile: p, bed: bedFromProfile(p.data) }));
  const usable = withBed.filter((p) => p.bed !== null);
  const selected = usable.find((p) => String(p.profile.id) === profileId) ?? null;
  const bed = selected?.bed ?? { x_mm: Number(bedX), y_mm: Number(bedY) };
  const bedValid = bed.x_mm > 0 && bed.y_mm > 0;
  const spacingN = Number(spacing);
  const spacingValid = spacing.trim() !== "" && spacingN >= 0;
  const items = files.map((f) => ({ file_id: f.id, copies: copies[f.id] ?? 1 }));
  const total = items.reduce((sum, i) => sum + i.copies, 0);
  const copiesValid = items.every((i) => Number.isInteger(i.copies) && i.copies >= 1) && total <= MAX_OBJECTS;

  const request: CombineRequest | null = useMemo(() => {
    if (!open || !bedValid || !spacingValid || !copiesValid) return null;
    return {
      items,
      layout,
      spacing_mm: spacingN,
      printer_profile_id: selected ? selected.profile.id : null,
      bed: selected ? null : bed,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, bedValid, spacingValid, copiesValid, JSON.stringify(items), layout, spacingN, selected?.profile.id, bed.x_mm, bed.y_mm]);

  const plan = useCombinePlan(slug, request);
  const planData = plan.data;
  const planError = plan.error instanceof ApiError ? plan.error.detail : plan.error ? "Could not plan the layout" : null;
  const canSubmit = request !== null && planData?.fits === true && !combine.isPending;

  function reset() {
    setCopies({});
    setName("");
  }

  function submit() {
    if (!request) return;
    combine.mutate(
      { ...request, name: name.trim() || null },
      {
        onSuccess: (res) => {
          toast.success(`Created ${res.file ? baseName(res.file.rel_path) : "combined plate"} (${total} objects)`);
          onOpenChange(false);
          reset();
        },
        onError: (e) => toast.error(e instanceof ApiError ? e.detail : "Could not combine the files"),
      },
    );
  }

  const viewW = Math.max(bed.x_mm, planData?.fits === false ? planData.required_bed.x_mm : 0) || 1;
  const viewH = Math.max(bed.y_mm, planData?.fits === false ? planData.required_bed.y_mm : 0) || 1;
  const colorOf = (fileId: number) => COLORS[files.findIndex((f) => f.id === fileId) % COLORS.length];

  return (
    <FormDialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) reset();
      }}
      dirty={name !== "" || Object.keys(copies).length > 0}
      busy={combine.isPending}
    >
      <DialogHeader>
        <DialogTitle>Combine onto one plate</DialogTitle>
        <DialogDescription>
          Arranges copies of the selected files on one bed and saves them as a single multi-object 3MF you can slice.
        </DialogDescription>
      </DialogHeader>

      <div className="grid gap-2">
        {files.map((f) => (
          <div key={f.id} className="flex items-center gap-2">
            <span className="size-2.5 shrink-0 rounded-sm" style={{ background: colorOf(f.id) }} />
            <Label htmlFor={`combine-copies-${f.id}`} className="min-w-0 flex-1 truncate font-mono text-xs font-normal" title={f.rel_path}>
              {baseName(f.rel_path)}
            </Label>
            <Input
              id={`combine-copies-${f.id}`}
              type="number"
              min={1}
              max={MAX_OBJECTS}
              aria-label={`Copies of ${baseName(f.rel_path)}`}
              value={copies[f.id] ?? 1}
              onChange={(e) => setCopies((prev) => ({ ...prev, [f.id]: Number(e.target.value) }))}
              className="w-20"
            />
          </div>
        ))}
        {total > MAX_OBJECTS ? (
          <p role="alert" className="text-xs text-destructive">
            At most {MAX_OBJECTS} objects per plate.
          </p>
        ) : null}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="col-span-2 grid gap-1.5">
          <Label htmlFor="combine-printer">Printer / bed</Label>
          <select
            id="combine-printer"
            value={profileId}
            onChange={(e) => setProfileId(e.target.value)}
            className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm"
          >
            <option value="">Custom bed size</option>
            {usable.map(({ profile, bed: b }) => (
              <option key={profile.id} value={String(profile.id)}>
                {profile.name} ({b!.x_mm} × {b!.y_mm} mm)
              </option>
            ))}
          </select>
        </div>
        {selected ? null : (
          <>
            <div className="grid gap-1.5">
              <Label htmlFor="combine-bed-x">Bed X (mm)</Label>
              <Input id="combine-bed-x" type="number" min={1} value={bedX} onChange={(e) => setBedX(e.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="combine-bed-y">Bed Y (mm)</Label>
              <Input id="combine-bed-y" type="number" min={1} value={bedY} onChange={(e) => setBedY(e.target.value)} />
            </div>
          </>
        )}
        <div className="grid gap-1.5">
          <Label htmlFor="combine-spacing">Spacing (mm)</Label>
          <Input id="combine-spacing" type="number" min={0} value={spacing} onChange={(e) => setSpacing(e.target.value)} />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="combine-layout">Layout</Label>
          <select
            id="combine-layout"
            value={layout}
            onChange={(e) => setLayout(e.target.value as "grid" | "auto")}
            className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm"
          >
            <option value="auto">Auto (packed)</option>
            <option value="grid">Grid</option>
          </select>
        </div>
        <div className="col-span-2 grid gap-1.5">
          <Label htmlFor="combine-name">File name (optional)</Label>
          <Input id="combine-name" placeholder="combined" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />
        </div>
      </div>

      <div className="grid gap-1.5">
        <svg
          role="img"
          aria-label="Fit preview"
          viewBox={`0 0 ${viewW} ${viewH}`}
          preserveAspectRatio="xMidYMid meet"
          className="max-h-56 w-full rounded border bg-muted/30"
        >
          <rect x={0} y={0} width={bed.x_mm || 0} height={bed.y_mm || 0} fill="none" stroke="currentColor" strokeWidth={Math.max(viewW, viewH) / 200} className="text-muted-foreground" />
          {(planData?.placements ?? []).map((p, i) => (
            <rect
              key={i}
              data-testid="combine-rect"
              x={p.x_mm}
              y={p.y_mm}
              width={p.w_mm}
              height={p.d_mm}
              fill={colorOf(p.file_id)}
              fillOpacity={planData?.fits ? 0.7 : 0.35}
              stroke={planData?.fits ? "none" : "#ef4444"}
              strokeWidth={Math.max(viewW, viewH) / 400}
            />
          ))}
        </svg>
        {planError ? (
          <p role="alert" className="text-xs text-destructive">
            {planError}
          </p>
        ) : planData ? (
          planData.fits ? (
            <p className="text-xs text-muted-foreground">
              Fits: {total} object{total === 1 ? "" : "s"} on {bed.x_mm} × {bed.y_mm} mm.
            </p>
          ) : (
            <p role="alert" className="text-xs text-destructive">
              Does not fit a {bed.x_mm} × {bed.y_mm} mm bed; needs about {planData.required_bed.x_mm} ×{" "}
              {planData.required_bed.y_mm} mm. Reduce copies or spacing.
            </p>
          )
        ) : plan.isFetching ? (
          <p className="text-xs text-muted-foreground">Planning…</p>
        ) : null}
      </div>

      <DialogFooter showCloseButton>
        <Button type="button" disabled={!canSubmit} onClick={submit}>
          Combine
        </Button>
      </DialogFooter>
    </FormDialog>
  );
}
