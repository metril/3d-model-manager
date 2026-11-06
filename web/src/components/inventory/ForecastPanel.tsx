/** Stock forecast: per-SKU runway, reorder points and queue demand. Used as the
 * Spools "Forecast" tab (`ForecastPanel`) and a Stats widget (`StockForecastWidget`). */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import {
  useForecast,
  useForecastConfig,
  useForecastSku,
  type Forecast,
  type ForecastSku,
  type SkuStatus,
} from "@/api/stockForecast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { SwitchRow } from "@/components/ui/switch-row";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const STATUS_VARIANT: Record<SkuStatus, "secondary" | "outline" | "destructive"> = {
  ok: "secondary",
  reorder: "outline",
  break: "destructive",
};

const skuName = (s: Pick<ForecastSku, "material" | "brand" | "color">) =>
  [s.brand, s.material, s.color].filter(Boolean).join(" ");

const days = (d: number | null, t: (k: string, o?: Record<string, unknown>) => string) =>
  d == null ? "–" : d >= 0 ? t("forecast.daysShort", { n: d }) : t("forecast.now");

function StatusBadge({ sku }: { sku: ForecastSku }) {
  const { t } = useTranslation();
  return (
    <Badge variant={STATUS_VARIANT[sku.status]} data-status={sku.status}>
      {t(`forecast.status.${sku.status}`)}
      {sku.snoozed && sku.status !== "ok" ? ` ${t("forecast.snoozedSuffix")}` : ""}
    </Badge>
  );
}

function Shortfalls({ data }: { data: Forecast }) {
  const { t } = useTranslation();
  if (data.queue_shortfalls.length === 0) return null;
  return (
    <ul className="space-y-1 rounded-md border border-destructive/40 p-3 text-sm" data-testid="queue-shortfalls">
      {data.queue_shortfalls.map((s) => (
        <li key={s.material}>
          {t("forecast.shortfall", { material: s.material, demand: Math.round(s.demand_g), stock: Math.round(s.stock_g) })}
        </li>
      ))}
    </ul>
  );
}

function ConfigForm({ data, canWrite }: { data: Forecast; canWrite: boolean }) {
  const { t } = useTranslation();
  const cfg = data.config;
  const save = useForecastConfig();
  const [lead, setLead] = useState(String(cfg.lead_time_days));
  const [margin, setMargin] = useState(String(cfg.safety_margin_value));
  // A failed save snaps the inputs back to the stored values.
  const patch = (p: Parameters<typeof save.mutate>[0]) =>
    save.mutate(p, {
      onError: (e) => {
        setLead(String(cfg.lead_time_days));
        setMargin(String(cfg.safety_margin_value));
        toast.error(e instanceof ApiError ? e.detail : t("forecast.saveError"));
      },
    });
  return (
    <div className="flex flex-wrap items-end gap-4 rounded-md border p-3 text-sm" data-testid="forecast-config">
      <label className="flex flex-col gap-1">
        {t("forecast.leadTime")}
        <Input
          className="w-24"
          type="number"
          min={0}
          value={lead}
          disabled={!canWrite}
          onChange={(e) => setLead(e.target.value)}
          onBlur={() => {
            if (lead.trim() === "") return setLead(String(cfg.lead_time_days)); // empty is not 0
            if (Number(lead) !== cfg.lead_time_days) patch({ lead_time_days: Math.max(0, Number(lead) || 0) });
          }}
        />
      </label>
      <label className="flex flex-col gap-1">
        {t("forecast.safetyMargin", { unit: cfg.safety_margin_unit })}
        <Input
          className="w-24"
          type="number"
          min={0}
          value={margin}
          disabled={!canWrite}
          onChange={(e) => setMargin(e.target.value)}
          onBlur={() => {
            if (margin.trim() === "") return setMargin(String(cfg.safety_margin_value));
            if (Number(margin) !== cfg.safety_margin_value) patch({ safety_margin_value: Math.max(0, Number(margin) || 0) });
          }}
        />
      </label>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={!canWrite}
        onClick={() => patch({ safety_margin_unit: cfg.safety_margin_unit === "days" ? "g" : "days" })}
      >
        {cfg.safety_margin_unit === "days" ? t("forecast.marginInGrams") : t("forecast.marginInDays")}
      </Button>
      <SwitchRow label={t("forecast.includeQueue")} checked={cfg.include_queue} disabled={!canWrite} onCheckedChange={(v) => patch({ include_queue: v })} />
      <SwitchRow label={t("forecast.notify")} checked={cfg.notify} disabled={!canWrite} onCheckedChange={(v) => patch({ notify: v })} />
    </div>
  );
}

function SkuRow({ sku, canWrite }: { sku: ForecastSku; canWrite: boolean }) {
  const { t } = useTranslation();
  const save = useForecastSku();
  const [lead, setLead] = useState(String(sku.lead_time_days));
  const key = { material: sku.material, brand: sku.brand, color: sku.color };
  return (
    <TableRow data-testid="forecast-row" data-status={sku.status}>
      <TableCell className="font-medium">{skuName(sku)}</TableCell>
      <TableCell className="tabular-nums">{Math.round(sku.remaining_g)} g</TableCell>
      <TableCell className="tabular-nums">{sku.demand_g > 0 ? `${Math.round(sku.demand_g)} g` : "–"}</TableCell>
      <TableCell className="tabular-nums">{sku.daily_rate_g == null ? "–" : `${sku.daily_rate_g} g/d`}</TableCell>
      <TableCell className="tabular-nums">{days(sku.days_remaining, t)}</TableCell>
      <TableCell className="tabular-nums">{Math.round(sku.reorder_point_g)} g</TableCell>
      <TableCell>
        <Input
          className="h-8 w-16"
          type="number"
          min={0}
          aria-label={t("forecast.leadFor", { name: skuName(sku) })}
          value={lead}
          disabled={!canWrite}
          onChange={(e) => setLead(e.target.value)}
          onBlur={() => {
            if (lead.trim() === "") return setLead(String(sku.lead_time_days)); // empty is not 0
            if (Number(lead) === sku.lead_time_days) return;
            save.mutate(
              { ...key, lead_time_days: Math.max(0, Number(lead) || 0) },
              { onError: () => setLead(String(sku.lead_time_days)) },
            );
          }}
        />
      </TableCell>
      <TableCell>
        <StatusBadge sku={sku} />
      </TableCell>
      <TableCell>
        {canWrite && sku.status !== "ok" ? (
          <Button size="sm" variant="ghost" onClick={() => save.mutate({ ...key, alerts_snoozed: !sku.snoozed })}>
            {sku.snoozed ? t("forecast.unsnooze") : t("forecast.snooze")}
          </Button>
        ) : null}
      </TableCell>
    </TableRow>
  );
}

export function ForecastPanel() {
  const { t } = useTranslation();
  const { can } = useAuth();
  const { data, isLoading } = useForecast();
  const canWrite = can("inventory:write");
  if (isLoading || !data) return <Skeleton className="h-40 w-full rounded-lg" />;
  return (
    <div className="space-y-4" data-testid="forecast-panel">
      <ConfigForm data={data} canWrite={canWrite} />
      <Shortfalls data={data} />
      {data.skus.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("forecast.empty")}</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("forecast.col.filament")}</TableHead>
              <TableHead>{t("forecast.col.inStock")}</TableHead>
              <TableHead>{t("forecast.col.queued")}</TableHead>
              <TableHead>{t("forecast.col.usage")}</TableHead>
              <TableHead>{t("forecast.col.runsOut")}</TableHead>
              <TableHead>{t("forecast.col.reorderAt")}</TableHead>
              <TableHead>{t("forecast.col.lead")}</TableHead>
              <TableHead>{t("forecast.col.status")}</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.skus.map((s) => (
              <SkuRow key={`${s.material}|${s.brand}|${s.color}`} sku={s} canWrite={canWrite} />
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

/** Compact stats-page widget: the SKUs closest to running out. */
export function StockForecastWidget(_props: unknown) {
  const { t } = useTranslation();
  const { data, isLoading } = useForecast();
  if (isLoading || !data) return <Skeleton className="h-24 w-full" />;
  if (data.skus.length === 0) return <p className="text-sm text-muted-foreground">{t("forecast.noSpools")}</p>;
  const top = data.skus.filter((s) => s.days_remaining != null || s.status !== "ok").slice(0, 6);
  return (
    <div className="space-y-2" data-testid="stock-widget">
      {data.alert_count > 0 ? <p className="text-xs text-muted-foreground">{t("forecast.needAttention", { count: data.alert_count })}</p> : null}
      {top.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("forecast.noHistory")}</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {top.map((s) => (
            <li key={`${s.material}|${s.brand}|${s.color}`} className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate">{skuName(s)}</span>
              <span className="tabular-nums text-muted-foreground">{days(s.days_remaining, t)}</span>
              <StatusBadge sku={s} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
