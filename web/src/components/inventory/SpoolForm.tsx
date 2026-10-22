/**
 * Create/edit dialog for a spool (P5 Task 9). Fully controlled (`open`/
 * `onOpenChange`) rather than owning its own `DialogTrigger` -- both
 * `SpoolsPage`'s "Add spool" button and `SlotAssignPopover`'s "New spool
 * from this tray" (Task 10) need to open it from elsewhere, the latter
 * with a `prefill` (type/colour/tray_uuid) rather than an existing spool.
 *
 * The "Weigh spool" helper computes `used = label − (gross − empty)` from
 * a gross-weight reading, so the user never has to do that subtraction by
 * hand -- it only activates once `empty_weight_g` is set, since the math
 * is meaningless without it.
 */
import { useEffect, useState, type FormEvent } from "react";

import { ApiError } from "@/api/client";
import { useMaterials } from "@/api/materials";
import { useCreateSpool, usePatchSpool } from "@/api/spools";
import type { MaterialKind, SpoolCreate, SpoolOut } from "@/api/types";
import { useAuth } from "@/api/auth";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { normalizeHex } from "@/components/ui/filament-chip";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

const MATERIAL_KINDS: MaterialKind[] = ["PLA", "PETG", "ABS", "ASA", "TPU", "Resin", "Other"];

export interface SpoolFormPrefill {
  material_type?: string;
  color_hex?: string | null;
  tray_uuid?: string | null;
}

function fieldsFor(spool: SpoolOut | undefined, prefill: SpoolFormPrefill | undefined) {
  return {
    materialId: spool?.material_id ?? null,
    materialType: spool?.material_type ?? prefill?.material_type ?? "PLA",
    brand: spool?.brand ?? "",
    colorHex: spool?.color_hex ?? prefill?.color_hex ?? "",
    colorName: spool?.color_name ?? "",
    labelWeight: String(spool?.label_weight_g ?? 1000),
    emptyWeight: spool?.empty_weight_g != null ? String(spool.empty_weight_g) : "",
    usedWeight: String(spool?.weight_used_g ?? 0),
    costPerKg: spool?.cost_per_kg != null ? String(spool.cost_per_kg) : "",
    location: spool?.location ?? "",
    lowStockG: String(spool?.low_stock_g ?? 100),
    trayUuid: spool?.tray_uuid ?? prefill?.tray_uuid ?? "",
  };
}

export function SpoolForm({
  open,
  onOpenChange,
  spool,
  prefill,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  spool?: SpoolOut;
  prefill?: SpoolFormPrefill;
  onSaved?: (spool: SpoolOut) => void;
}) {
  const isEdit = spool !== undefined;
  const { can } = useAuth();
  const canWrite = can("inventory:write");
  const materials = useMaterials();
  const createSpool = useCreateSpool();
  const patchSpool = usePatchSpool();
  const mutation = isEdit ? patchSpool : createSpool;

  const [fields, setFields] = useState(() => fieldsFor(spool, prefill));
  const [grossWeight, setGrossWeight] = useState("");

  useEffect(() => {
    if (open) {
      setFields(fieldsFor(spool, prefill));
      setGrossWeight("");
      createSpool.reset();
      patchSpool.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const empty = fields.emptyWeight.trim() === "" ? null : Number(fields.emptyWeight);
  const label = Number(fields.labelWeight) || 0;
  const gross = grossWeight.trim() === "" ? null : Number(grossWeight);
  const weighedUsed = empty != null && gross != null ? Math.max(0, label - (gross - empty)) : null;

  function applyWeighed() {
    if (weighedUsed == null) return;
    setFields((f) => ({ ...f, usedWeight: String(Math.round(weighedUsed * 100) / 100) }));
    setGrossWeight("");
  }

  function handleMaterialChange(materialId: string) {
    if (materialId === "none") {
      setFields((f) => ({ ...f, materialId: null }));
      return;
    }
    const material = (materials.data ?? []).find((m) => m.id === Number(materialId));
    setFields((f) => ({
      ...f,
      materialId: Number(materialId),
      materialType: material?.kind ?? f.materialType,
      colorHex: material?.color ?? f.colorHex,
    }));
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload: SpoolCreate = {
      material_id: fields.materialId,
      material_type: fields.materialType,
      brand: fields.brand.trim() || null,
      color_hex: fields.colorHex.trim() || null,
      color_name: fields.colorName.trim() || null,
      label_weight_g: Number(fields.labelWeight) || 0,
      empty_weight_g: fields.emptyWeight.trim() === "" ? null : Number(fields.emptyWeight),
      weight_used_g: Number(fields.usedWeight) || 0,
      cost_per_kg: fields.costPerKg.trim() === "" ? null : Number(fields.costPerKg),
      location: fields.location.trim() || null,
      low_stock_g: Number(fields.lowStockG) || 0,
      tray_uuid: fields.trayUuid.trim() || null,
    };
    if (isEdit) {
      patchSpool.mutate({ id: spool.id, payload }, { onSuccess: (s) => { onSaved?.(s); onOpenChange(false); } });
    } else {
      createSpool.mutate(payload, { onSuccess: (s) => { onSaved?.(s); onOpenChange(false); } });
    }
  }

  const swatch = normalizeHex(fields.colorHex);
  const rfidLocked = Boolean(spool?.tray_uuid) && isEdit;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>{isEdit ? "Edit spool" : "New spool"}</DialogTitle>
            <DialogDescription>
              Track a physical filament spool: label weight, used grams, and where it lives.
            </DialogDescription>
          </DialogHeader>

          <div className="flex max-h-[60vh] flex-col gap-4 overflow-y-auto py-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="spool-material">From material (optional)</Label>
              <Select
                aria-label="From material"
                value={fields.materialId != null ? String(fields.materialId) : "none"}
                onValueChange={handleMaterialChange}
                disabled={!canWrite}
              >
                <SelectTrigger id="spool-material" aria-label="From material" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">None</SelectItem>
                  {(materials.data ?? []).map((m) => (
                    <SelectItem key={m.id} value={String(m.id)}>
                      {m.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="spool-type">Type</Label>
                <Select
                  aria-label="Type"
                  value={fields.materialType}
                  onValueChange={(v) => setFields((f) => ({ ...f, materialType: v }))}
                  disabled={!canWrite}
                >
                  <SelectTrigger id="spool-type" aria-label="Type" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {MATERIAL_KINDS.map((kind) => (
                      <SelectItem key={kind} value={kind}>
                        {kind}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="spool-brand">Brand</Label>
                <Input
                  id="spool-brand"
                  value={fields.brand}
                  onChange={(e) => setFields((f) => ({ ...f, brand: e.target.value }))}
                  disabled={!canWrite}
                />
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="spool-color">Colour</Label>
              <div className="flex items-center gap-2">
                <span
                  aria-hidden="true"
                  className="size-6 shrink-0 rounded-full border border-border/60"
                  style={swatch ? { backgroundColor: swatch } : undefined}
                />
                <Input
                  id="spool-color"
                  placeholder="#RRGGBB"
                  value={fields.colorHex}
                  onChange={(e) => setFields((f) => ({ ...f, colorHex: e.target.value }))}
                  disabled={!canWrite}
                  className="w-32"
                />
                <Input
                  aria-label="Colour name"
                  placeholder="Colour name"
                  value={fields.colorName}
                  onChange={(e) => setFields((f) => ({ ...f, colorName: e.target.value }))}
                  disabled={!canWrite}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="spool-label-weight">Label weight (g)</Label>
                <Input
                  id="spool-label-weight"
                  type="number"
                  value={fields.labelWeight}
                  onChange={(e) => setFields((f) => ({ ...f, labelWeight: e.target.value }))}
                  disabled={!canWrite}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="spool-empty-weight">Empty spool weight (g)</Label>
                <Input
                  id="spool-empty-weight"
                  type="number"
                  value={fields.emptyWeight}
                  onChange={(e) => setFields((f) => ({ ...f, emptyWeight: e.target.value }))}
                  disabled={!canWrite}
                />
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="spool-used-weight">Used (g)</Label>
              <Input
                id="spool-used-weight"
                type="number"
                value={fields.usedWeight}
                onChange={(e) => setFields((f) => ({ ...f, usedWeight: e.target.value }))}
                disabled={!canWrite}
              />
            </div>

            <div className="flex flex-col gap-1.5 rounded-[6px] border p-2.5">
              <Label htmlFor="spool-gross-weight">Weigh spool (gross g, on a scale)</Label>
              <div className="flex items-center gap-2">
                <Input
                  id="spool-gross-weight"
                  type="number"
                  value={grossWeight}
                  onChange={(e) => setGrossWeight(e.target.value)}
                  disabled={!canWrite || empty == null}
                  className="w-32"
                  placeholder={empty == null ? "Set empty weight first" : "e.g. 650"}
                />
                <span className="font-mono text-xs tabular-nums text-muted-foreground">
                  {weighedUsed != null ? `= ${weighedUsed.toFixed(1)} g used` : ""}
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={!canWrite || weighedUsed == null}
                  onClick={applyWeighed}
                >
                  Apply
                </Button>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="spool-cost">Cost / kg</Label>
                <Input
                  id="spool-cost"
                  type="number"
                  value={fields.costPerKg}
                  onChange={(e) => setFields((f) => ({ ...f, costPerKg: e.target.value }))}
                  disabled={!canWrite}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="spool-low-stock">Low-stock threshold (g)</Label>
                <Input
                  id="spool-low-stock"
                  type="number"
                  value={fields.lowStockG}
                  onChange={(e) => setFields((f) => ({ ...f, lowStockG: e.target.value }))}
                  disabled={!canWrite}
                />
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="spool-location">Location</Label>
              <Input
                id="spool-location"
                value={fields.location}
                onChange={(e) => setFields((f) => ({ ...f, location: e.target.value }))}
                disabled={!canWrite}
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="spool-tray-uuid">Tray UUID (RFID)</Label>
              <Input
                id="spool-tray-uuid"
                value={fields.trayUuid}
                onChange={(e) => setFields((f) => ({ ...f, trayUuid: e.target.value }))}
                disabled={!canWrite || rfidLocked}
                readOnly={rfidLocked}
              />
            </div>

            {mutation.isError ? (
              <p role="alert" className="text-sm text-destructive">
                {mutation.error instanceof ApiError ? mutation.error.detail : "Could not save this spool"}
              </p>
            ) : null}
          </div>

          {canWrite ? (
            <DialogFooter>
              <Button type="submit" disabled={mutation.isPending}>
                {mutation.isPending ? "Saving..." : isEdit ? "Save changes" : "Add spool"}
              </Button>
            </DialogFooter>
          ) : null}
        </form>
      </DialogContent>
    </Dialog>
  );
}
