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
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { useEffect, useState } from "react";

import { toast } from "sonner";

import { useRepriceRuns } from "@/api/archives";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { HaEntity } from "@/api/types";
import { EntityCombobox } from "@/components/ui/entity-combobox";
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

type DraftErrors = Partial<Record<keyof Draft | "currency", string>>;

function seedDraft(settings: AppSettings): Draft {
  return {
    filament_cost_per_kg: String(settings.filament_cost_per_kg),
    machine_cost_per_hour: String(settings.machine_cost_per_hour),
    energy_cost_per_kwh: String(settings.energy_cost_per_kwh),
  };
}

function validateField(raw: string, t: TFunction): string | undefined {
  const trimmed = raw.trim();
  if (trimmed === "") return t("settingsPrintCost.required");
  const n = Number(trimmed);
  if (Number.isNaN(n)) return t("settingsPrintCost.mustBeNumber");
  if (n < 0) return t("settingsPrintCost.mustBePositive");
  return undefined;
}

/** Monetary sensors, or anything priced per kWh. */
const isPriceSensor = (e: HaEntity) =>
  e.device_class === "monetary" || (e.unit ?? "").toLowerCase().includes("/kwh");

export function PrintCostCard() {
  const { t } = useTranslation();
  const settings = useAppSettings();
  const update = useUpdateAppSettings();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [errors, setErrors] = useState<DraftErrors>({});
  const [priceSource, setPriceSource] = useState<"fixed" | "homeassistant" | null>(null);
  const [priceEntity, setPriceEntity] = useState<string | null>(null);
  const [currency, setCurrencyDraft] = useState<string | null>(null);
    const reprice = useRepriceRuns();
  const [repriceCount, setRepriceCount] = useState<number | null>(null);

  useEffect(() => {
    if (settings.data && draft === null) {
      setDraft(seedDraft(settings.data));
      setPriceSource(settings.data.energy_price_source);
      setPriceEntity(settings.data.energy_price_ha_entity);
      setCurrencyDraft(settings.data.currency ?? "");
    }
  }, [settings.data, draft]);

  if (settings.isLoading || draft === null) return <Skeleton className="h-56 w-full rounded-xl" />;

  if (settings.isError || !settings.data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t("settingsPrintCost.title")}</CardTitle>
          <CardDescription>
            {settings.error instanceof ApiError ? settings.error.detail : t("settingsPrintCost.loadError")}
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
      const message = validateField(current[key], t);
      if (message) next[key] = message;
    });
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  function save() {
    if (!settings.data || !draft) return;
    if (!validate(draft)) return;
    const code = (currency ?? "").trim().toUpperCase();
    if (code !== "" && !/^[A-Z]{3}$/.test(code)) {
      setErrors((prev) => ({ ...prev, currency: t("settingsPrintCost.currencyInvalid") }));
      return;
    }
    update.mutate({
      ...settings.data,
      filament_cost_per_kg: Number(draft.filament_cost_per_kg),
      machine_cost_per_hour: Number(draft.machine_cost_per_hour),
      energy_cost_per_kwh: Number(draft.energy_cost_per_kwh),
      energy_price_source: priceSource ?? "fixed",
      energy_price_ha_entity: (priceEntity ?? "").trim(),
      currency: code,
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settingsPrintCost.title")}</CardTitle>
        <CardDescription>
          {t("settingsPrintCost.description")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="filament-cost">{t("settingsPrintCost.filament")}</Label>
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
            <Label htmlFor="machine-cost">{t("settingsPrintCost.machine")}</Label>
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
              <p className="text-xs text-muted-foreground">{t("settingsPrintCost.machineHint")}</p>
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="energy-cost">{t("settingsPrintCost.energy")}</Label>
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
                  ? t("settingsPrintCost.energyFallback")
                  : t("settingsPrintCost.energyZero")}
              </p>
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="currency-code">{t("settingsPrintCost.currency")}</Label>
            <Input
              id="currency-code"
              maxLength={3}
              placeholder="USD"
              value={currency ?? ""}
              onChange={(e) => {
                setCurrencyDraft(e.target.value);
                setErrors((prev) => (prev.currency ? { ...prev, currency: undefined } : prev));
              }}
              aria-invalid={Boolean(errors.currency)}
            />
            {errors.currency ? (
              <p role="alert" className="text-xs text-destructive">
                {errors.currency}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">{t("settingsPrintCost.currencyHint")}</p>
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="energy-price-source">{t("settingsPrintCost.priceSource")}</Label>
            <select
              id="energy-price-source"
              className="h-9 rounded-md border border-input bg-background px-3 text-sm"
              value={priceSource ?? "fixed"}
              onChange={(e) => setPriceSource(e.target.value as "fixed" | "homeassistant")}
            >
              <option value="fixed">{t("settingsPrintCost.fixed")}</option>
              <option value="homeassistant">{t("settingsPrintCost.haSensor")}</option>
            </select>
          </div>
          {priceSource === "homeassistant" ? (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="energy-price-entity">{t("settingsPrintCost.entity")}</Label>
              <EntityCombobox
                id="energy-price-entity"
                ariaLabel={t("settingsPrintCost.entity")}
                domain={["sensor"]}
                filter={isPriceSensor}
                placeholder="sensor.electricity_price"
                value={priceEntity ?? ""}
                onChange={setPriceEntity}
              />
              <p className="text-xs text-muted-foreground">
                {t("settingsPrintCost.entitiesHint")}
              </p>
            </div>
          ) : null}
        </div>

        <div className="flex items-center gap-3">
          <Button type="button" disabled={update.isPending} onClick={save}>
            {update.isPending ? t("settingsPrintCost.saving") : t("common.save")}
          </Button>
          {update.isSuccess && <p className="text-sm text-success">{t("settingsPrintCost.saved")}</p>}
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
                    ? toast(t("settingsPrintCost.noneToReprice"))
                    : setRepriceCount(res.repriced),
                onError: () => toast.error(t("settingsPrintCost.checkError")),
              })
            }
          >
            {t("settingsPrintCost.reprice")}
          </Button>
          <p className="text-xs text-muted-foreground">
            {t("settingsPrintCost.repriceHint")}
          </p>
        </div>
        <ConfirmDialog
          open={repriceCount !== null}
          onOpenChange={(o) => !o && setRepriceCount(null)}
          title={t("settingsPrintCost.repriceTitle", { count: repriceCount ?? 0 })}
          description={t("settingsPrintCost.repriceDesc")}
          confirmLabel={t("settingsPrintCost.repriceConfirm", { n: repriceCount ?? 0 })}
          onConfirm={() =>
            reprice.mutate(false, {
              onSuccess: (res) =>
                toast.success(
                  t("settingsPrintCost.repriced", {
                    count: res.repriced,
                    delta: `${res.delta >= 0 ? "+" : ""}${res.delta.toFixed(2)}`,
                  }),
                ),
              onError: () => toast.error(t("settingsPrintCost.repriceFailed")),
            })
          }
        />
        {update.isError && (
          <p role="alert" className="text-sm text-destructive">
            {update.error instanceof ApiError ? update.error.detail : t("settingsPrintCost.saveError")}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
