/**
 * Print cost estimate rates card (R11-B item 14): the General tab's editor
 * for `filament_cost_per_kg`/`machine_cost_per_hour` in the DB-backed
 * `AppSettings` (`GET`/`PUT /settings/app`) -- the two rates the server
 * (`app.services.costing`) prices every run and plate estimate with.
 * Currency-agnostic (no `currency_symbol` setting
 * yet -- out of scope), so labels just say "per kg"/"per hour". Mirrors
 * `AutomationCard`'s draft/save/alert/"Saved." card conventions, spreading
 * the OTHER four `AppSettings` fields through unchanged on every `PUT`
 * (full replace, not a per-field patch).
 */
import { useEffect, useState } from "react";

import { toast } from "sonner";

import { useRepriceRuns } from "@/api/archives";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useHaEntities } from "@/api/spoolAdmin";
import { useAppSettings, useUpdateAppSettings } from "@/api/appSettings";
import { ApiError } from "@/api/client";
import type { AppSettings } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

interface Draft {
  filament_cost_per_kg: string;
  machine_cost_per_hour: string;
  energy_cost_per_kwh: string;
}

type DraftErrors = Partial<Record<keyof Draft, string>>;

function seedDraft(settings: AppSettings): Draft {
  return {
    filament_cost_per_kg: String(settings.filament_cost_per_kg),
    machine_cost_per_hour: String(settings.machine_cost_per_hour),
    energy_cost_per_kwh: String(settings.energy_cost_per_kwh),
  };
}

function validateField(raw: string): string | undefined {
  const trimmed = raw.trim();
  if (trimmed === "") return "Required -- enter 0 to ignore this cost.";
  const n = Number(trimmed);
  if (Number.isNaN(n)) return "Must be a number.";
  if (n < 0) return "Must be 0 or greater.";
  return undefined;
}

export function PrintCostCard() {
  const settings = useAppSettings();
  const update = useUpdateAppSettings();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [errors, setErrors] = useState<DraftErrors>({});
  const [priceSource, setPriceSource] = useState<"fixed" | "homeassistant" | null>(null);
  const [priceEntity, setPriceEntity] = useState<string | null>(null);
  const haEntities = useHaEntities(priceSource === "homeassistant");
  const reprice = useRepriceRuns();
  const [repriceCount, setRepriceCount] = useState<number | null>(null);

  useEffect(() => {
    if (settings.data && draft === null) {
      setDraft(seedDraft(settings.data));
      setPriceSource(settings.data.energy_price_source);
      setPriceEntity(settings.data.energy_price_ha_entity);
    }
  }, [settings.data, draft]);

  if (settings.isLoading || draft === null) return <Skeleton className="h-56 w-full rounded-xl" />;

  if (settings.isError || !settings.data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Print cost estimate</CardTitle>
          <CardDescription>
            {settings.error instanceof ApiError ? settings.error.detail : "Couldn't load these settings."}
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  function setField(key: keyof Draft, value: string) {
    setDraft((prev) => (prev ? { ...prev, [key]: value } : prev));
    setErrors((prev) => (prev[key] ? { ...prev, [key]: undefined } : prev));
  }

  function validate(current: Draft): boolean {
    const next: DraftErrors = {};
    (Object.keys(current) as (keyof Draft)[]).forEach((key) => {
      const message = validateField(current[key]);
      if (message) next[key] = message;
    });
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  function save() {
    if (!settings.data || !draft) return;
    if (!validate(draft)) return;
    update.mutate({
      ...settings.data,
      filament_cost_per_kg: Number(draft.filament_cost_per_kg),
      machine_cost_per_hour: Number(draft.machine_cost_per_hour),
      energy_cost_per_kwh: Number(draft.energy_cost_per_kwh),
      energy_price_source: priceSource ?? "fixed",
      energy_price_ha_entity: (priceEntity ?? "").trim(),
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Print cost estimate</CardTitle>
        <CardDescription>
          Used to estimate a print's cost from its filament weight and duration. No currency symbol
          -- enter whatever unit you use.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="filament-cost">Filament cost (per kg)</Label>
            <Input
              id="filament-cost"
              type="number"
              min={0}
              step="any"
              value={draft.filament_cost_per_kg}
              onChange={(e) => setField("filament_cost_per_kg", e.target.value)}
              aria-invalid={Boolean(errors.filament_cost_per_kg)}
            />
            {errors.filament_cost_per_kg ? (
              <p role="alert" className="text-xs text-destructive">
                {errors.filament_cost_per_kg}
              </p>
            ) : null}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="machine-cost">Machine cost (per hour)</Label>
            <Input
              id="machine-cost"
              type="number"
              min={0}
              step="any"
              value={draft.machine_cost_per_hour}
              onChange={(e) => setField("machine_cost_per_hour", e.target.value)}
              aria-invalid={Boolean(errors.machine_cost_per_hour)}
            />
            {errors.machine_cost_per_hour ? (
              <p role="alert" className="text-xs text-destructive">
                {errors.machine_cost_per_hour}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">0 = don&apos;t factor in machine time.</p>
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="energy-cost">Energy cost (per kWh)</Label>
            <Input
              id="energy-cost"
              type="number"
              min={0}
              step="any"
              value={draft.energy_cost_per_kwh}
              onChange={(e) => setField("energy_cost_per_kwh", e.target.value)}
              aria-invalid={Boolean(errors.energy_cost_per_kwh)}
            />
            {errors.energy_cost_per_kwh ? (
              <p role="alert" className="text-xs text-destructive">
                {errors.energy_cost_per_kwh}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">
                {priceSource === "homeassistant"
                  ? "Fallback when the Home Assistant price sensor is unavailable."
                  : "0 = don't price Home Assistant energy readings."}
              </p>
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="energy-price-source">Energy price source</Label>
            <select
              id="energy-price-source"
              className="h-9 rounded-md border border-input bg-background px-3 text-sm"
              value={priceSource ?? "fixed"}
              onChange={(e) => setPriceSource(e.target.value as "fixed" | "homeassistant")}
            >
              <option value="fixed">Fixed price</option>
              <option value="homeassistant">Home Assistant sensor</option>
            </select>
          </div>
          {priceSource === "homeassistant" ? (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="energy-price-entity">Price sensor entity</Label>
              <Input
                id="energy-price-entity"
                list="energy-price-entities"
                placeholder="sensor.electricity_price"
                value={priceEntity ?? ""}
                onChange={(e) => setPriceEntity(e.target.value)}
              />
              <datalist id="energy-price-entities">
                {(haEntities.data ?? []).map((ent) => (
                  <option key={ent.entity_id} value={ent.entity_id}>
                    {ent.friendly_name ?? ent.entity_id}
                  </option>
                ))}
              </datalist>
              <p className="text-xs text-muted-foreground">
                {haEntities.isError
                  ? "Couldn't list Home Assistant entities -- type the entity id."
                  : "Sampled at print start and end; the average prices the run."}
              </p>
            </div>
          ) : null}
        </div>

        <div className="flex items-center gap-3">
          <Button type="button" disabled={update.isPending} onClick={save}>
            {update.isPending ? "Saving…" : "Save"}
          </Button>
          {update.isSuccess && <p className="text-sm text-success">Saved.</p>}
        </div>
        <div className="flex flex-wrap items-center gap-3 border-t pt-4">
          <Button
            type="button"
            variant="outline"
            disabled={reprice.isPending}
            onClick={() =>
              reprice.mutate(true, {
                onSuccess: (res) =>
                  res.repriced === 0
                    ? toast("No past runs need repricing.")
                    : setRepriceCount(res.repriced),
                onError: () => toast.error("Could not check past runs."),
              })
            }
          >
            Reprice past runs
          </Button>
          <p className="text-xs text-muted-foreground">
            Recompute past runs&apos; filament cost from each spool&apos;s current price. Machine,
            wear and energy costs are kept.
          </p>
        </div>
        <ConfirmDialog
          open={repriceCount !== null}
          onOpenChange={(o) => !o && setRepriceCount(null)}
          title={`Reprice ${repriceCount ?? 0} past run${repriceCount === 1 ? "" : "s"}?`}
          description="Their filament cost is replaced using the spools' current price per kg. This can't be undone."
          confirmLabel={`Reprice ${repriceCount ?? 0}`}
          onConfirm={() =>
            reprice.mutate(false, {
              onSuccess: (res) =>
                toast.success(
                  `Repriced ${res.repriced} run${res.repriced === 1 ? "" : "s"} (${res.delta >= 0 ? "+" : ""}${res.delta.toFixed(2)})`,
                ),
              onError: () => toast.error("Repricing failed."),
            })
          }
        />
        {update.isError && (
          <p role="alert" className="text-sm text-destructive">
            {update.error instanceof ApiError ? update.error.detail : "Could not save these settings."}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
