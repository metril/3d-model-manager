/**
 * Bulk edit for selected spools (T5). Only fields whose "apply" checkbox is
 * ticked are sent in `update`; per-id failures from the response are listed
 * after submit and the dialog stays open so the user can see them.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useBulkUpdateSpools, useLocations } from "@/api/spoolAdmin";
import type { BulkResult, BulkSpoolUpdate, MaterialKind } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DialogClose, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

const MATERIAL_KINDS: MaterialKind[] = ["PLA", "PETG", "ABS", "ASA", "TPU", "Resin", "Other"];
type Key = "material_type" | "brand" | "color_hex" | "color_name" | "location_id" | "cost_per_kg" | "low_stock_g";
const NO_LOCATION = "none";

function buildBulkUpdate(on: Partial<Record<Key, boolean>>, vals: Record<Key, string>): BulkSpoolUpdate {
  const out: BulkSpoolUpdate = {};
  const num = (v: string) => (v.trim() === "" ? null : Number(v));
  if (on.material_type) out.material_type = vals.material_type;
  if (on.brand) out.brand = vals.brand.trim() || null;
  if (on.color_hex) out.color_hex = vals.color_hex.trim() || null;
  if (on.color_name) out.color_name = vals.color_name.trim() || null;
  if (on.location_id) out.location_id = vals.location_id === NO_LOCATION ? null : Number(vals.location_id);
  if (on.cost_per_kg) out.cost_per_kg = num(vals.cost_per_kg);
  if (on.low_stock_g) out.low_stock_g = Number(vals.low_stock_g) || 0;
  return out;
}

const EMPTY: Record<Key, string> = {
  material_type: "PLA",
  brand: "",
  color_hex: "",
  color_name: "",
  location_id: NO_LOCATION,
  cost_per_kg: "",
  low_stock_g: "100",
};

export function BulkEditDialog({
  ids,
  open,
  onOpenChange,
  onDone,
}: {
  ids: number[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDone?: () => void;
}) {
  const { t } = useTranslation();
  const locations = useLocations();
  const bulk = useBulkUpdateSpools();
  const [on, setOn] = useState<Partial<Record<Key, boolean>>>({});
  const [vals, setVals] = useState(EMPTY);
  const [result, setResult] = useState<BulkResult | null>(null);

  useEffect(() => {
    if (open) {
      setOn({});
      setVals(EMPTY);
      setResult(null);
      bulk.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const update = buildBulkUpdate(on, vals);
  const nothing = Object.keys(update).length === 0;
  const failures = result?.results.filter((r) => !r.ok) ?? [];

  function submit() {
    bulk.mutate(
      { ids, update },
      {
        onSuccess: (res) => {
          setResult(res);
          const bad = res.results.filter((r) => !r.ok).length + res.not_found.length;
          if (bad === 0) {
            toast.success(t("spoolAdmin.bulk.updated", { count: res.results.length }));
            onDone?.();
            onOpenChange(false);
          }
        },
      },
    );
  }

  function row(key: Key, label: string, control: React.ReactNode) {
    return (
      <div className="grid grid-cols-[auto_8rem_1fr] items-center gap-2">
        <Checkbox
          aria-label={t("spoolAdmin.bulk.apply", { field: label })}
          checked={on[key] === true}
          onCheckedChange={(c) => setOn((o) => ({ ...o, [key]: c === true }))}
        />
        <span className="text-sm">{label}</span>
        {control}
      </div>
    );
  }
  const set = (key: Key) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setVals((v) => ({ ...v, [key]: e.target.value }));
    setOn((o) => ({ ...o, [key]: true }));
  };

  return (
    <FormDialog open={open} onOpenChange={onOpenChange} dirty={Object.values(on).some(Boolean)} busy={bulk.isPending} className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("spoolAdmin.bulk.title", { count: ids.length })}</DialogTitle>
          <DialogDescription>{t("spoolAdmin.bulk.desc")}</DialogDescription>
        </DialogHeader>
        <div className="flex max-h-[60vh] flex-col gap-3 overflow-y-auto py-2">
          {row(
            "material_type",
            t("spoolAdmin.f.material"),
            <Select
              aria-label={t("spoolAdmin.f.material")}
              value={vals.material_type}
              onValueChange={(v) => {
                setVals((s) => ({ ...s, material_type: v }));
                setOn((o) => ({ ...o, material_type: true }));
              }}
            >
              <SelectTrigger aria-label={t("spoolAdmin.f.material")} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MATERIAL_KINDS.map((k) => (
                  <SelectItem key={k} value={k}>
                    {k}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>,
          )}
          {row("brand", t("spoolAdmin.f.brand"), <Input aria-label={t("spoolAdmin.f.brand")} value={vals.brand} onChange={set("brand")} />)}
          {row(
            "color_hex",
            t("spoolAdmin.f.colorHex"),
            <Input aria-label={t("spoolAdmin.f.colorHex")} placeholder="#RRGGBB" value={vals.color_hex} onChange={set("color_hex")} />,
          )}
          {row(
            "color_name",
            t("spoolAdmin.f.colorName"),
            <Input aria-label={t("spoolAdmin.f.colorName")} value={vals.color_name} onChange={set("color_name")} />,
          )}
          {row(
            "location_id",
            t("spoolAdmin.f.location"),
            <Select
              aria-label={t("spoolAdmin.f.location")}
              value={vals.location_id}
              onValueChange={(v) => {
                setVals((s) => ({ ...s, location_id: v }));
                setOn((o) => ({ ...o, location_id: true }));
              }}
            >
              <SelectTrigger aria-label={t("spoolAdmin.f.location")} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_LOCATION}>{t("spoolAdmin.common.none")}</SelectItem>
                {(locations.data ?? []).map((l) => (
                  <SelectItem key={l.id} value={String(l.id)}>
                    {l.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>,
          )}
          {row(
            "cost_per_kg",
            t("spoolAdmin.f.costPerKg"),
            <Input type="number" aria-label={t("spoolAdmin.f.costPerKg")} value={vals.cost_per_kg} onChange={set("cost_per_kg")} />,
          )}
          {row(
            "low_stock_g",
            t("spoolAdmin.f.lowStock"),
            <Input type="number" aria-label={t("spoolAdmin.f.lowStock")} value={vals.low_stock_g} onChange={set("low_stock_g")} />,
          )}

          {bulk.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {bulk.error instanceof ApiError ? bulk.error.detail : t("spoolAdmin.bulk.failed")}
            </p>
          ) : null}
          {result && (failures.length > 0 || result.not_found.length > 0) ? (
            <div role="alert" className="rounded-md border border-destructive/40 p-2 text-sm">
              <p className="font-medium text-destructive">{t("spoolAdmin.bulk.someFailed")}</p>
              <ul className="mt-1 list-disc pl-5 text-xs">
                {failures.map((f) => (
                  <li key={f.id}>
                    #{f.id}: {f.error ?? t("spoolAdmin.bulk.unknownError")}
                  </li>
                ))}
                {result.not_found.map((id) => (
                  <li key={`nf-${id}`}>
                    #{id}: {t("spoolAdmin.bulk.notFound")}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button type="button" variant="outline">
            {result ? t("spoolAdmin.common.close") : t("spoolAdmin.common.cancel")}
          </Button>
          </DialogClose>
          <Button type="button" disabled={nothing || bulk.isPending} onClick={submit}>
            {bulk.isPending ? t("spoolAdmin.common.saving") : t("spoolAdmin.bulk.apply_btn", { count: ids.length })}
          </Button>
        </DialogFooter>
    </FormDialog>
  );
}
