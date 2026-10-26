/** Stock forecast: per-SKU runway, reorder points and queue demand. Used as the
 * Spools "Forecast" tab (`ForecastPanel`) and a Stats widget (`StockForecastWidget`). */
import { useState } from "react";
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
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const STATUS_LABEL: Record<SkuStatus, string> = { ok: "OK", reorder: "Reorder", break: "Stock break" };
const STATUS_VARIANT: Record<SkuStatus, "secondary" | "outline" | "destructive"> = {
  ok: "secondary",
  reorder: "outline",
  break: "destructive",
};

const skuName = (s: Pick<ForecastSku, "material" | "brand" | "color">) =>
  [s.brand, s.material, s.color].filter(Boolean).join(" ");

const days = (d: number | null) => (d == null ? "–" : d >= 0 ? `${d} d` : "now");

function StatusBadge({ sku }: { sku: ForecastSku }) {
  return (
    <Badge variant={STATUS_VARIANT[sku.status]} data-status={sku.status}>
      {STATUS_LABEL[sku.status]}
      {sku.snoozed && sku.status !== "ok" ? " (snoozed)" : ""}
    </Badge>
  );
}

function Shortfalls({ data }: { data: Forecast }) {
  if (data.queue_shortfalls.length === 0) return null;
  return (
    <ul className="space-y-1 rounded-md border border-destructive/40 p-3 text-sm" data-testid="queue-shortfalls">
      {data.queue_shortfalls.map((s) => (
        <li key={s.material}>
          Queued {s.material} needs {Math.round(s.demand_g)} g but only {Math.round(s.stock_g)} g is in stock.
        </li>
      ))}
    </ul>
  );
}

function ConfigForm({ data, canWrite }: { data: Forecast; canWrite: boolean }) {
  const cfg = data.config;
  const save = useForecastConfig();
  const [lead, setLead] = useState(String(cfg.lead_time_days));
  const [margin, setMargin] = useState(String(cfg.safety_margin_value));
  const patch = (p: Parameters<typeof save.mutate>[0]) =>
    save.mutate(p, { onError: (e) => toast.error(e instanceof ApiError ? e.detail : "Could not save") });
  return (
    <div className="flex flex-wrap items-end gap-4 rounded-md border p-3 text-sm" data-testid="forecast-config">
      <label className="flex flex-col gap-1">
        Lead time (days)
        <Input
          className="w-24"
          type="number"
          min={0}
          value={lead}
          disabled={!canWrite}
          onChange={(e) => setLead(e.target.value)}
          onBlur={() => Number(lead) !== cfg.lead_time_days && patch({ lead_time_days: Math.max(0, Number(lead) || 0) })}
        />
      </label>
      <label className="flex flex-col gap-1">
        Safety margin ({cfg.safety_margin_unit})
        <Input
          className="w-24"
          type="number"
          min={0}
          value={margin}
          disabled={!canWrite}
          onChange={(e) => setMargin(e.target.value)}
          onBlur={() => Number(margin) !== cfg.safety_margin_value && patch({ safety_margin_value: Math.max(0, Number(margin) || 0) })}
        />
      </label>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={!canWrite}
        onClick={() => patch({ safety_margin_unit: cfg.safety_margin_unit === "days" ? "g" : "days" })}
      >
        Margin in {cfg.safety_margin_unit === "days" ? "grams" : "days"}
      </Button>
      <label className="flex items-center gap-2">
        <Switch checked={cfg.include_queue} disabled={!canWrite} onCheckedChange={(v) => patch({ include_queue: v })} />
        Include queued prints
      </label>
      <label className="flex items-center gap-2">
        <Switch checked={cfg.notify} disabled={!canWrite} onCheckedChange={(v) => patch({ notify: v })} />
        Notify on reorder
      </label>
    </div>
  );
}

function SkuRow({ sku, canWrite }: { sku: ForecastSku; canWrite: boolean }) {
  const save = useForecastSku();
  const [lead, setLead] = useState(String(sku.lead_time_days));
  const key = { material: sku.material, brand: sku.brand, color: sku.color };
  return (
    <TableRow data-testid="forecast-row" data-status={sku.status}>
      <TableCell className="font-medium">{skuName(sku)}</TableCell>
      <TableCell className="tabular-nums">{Math.round(sku.remaining_g)} g</TableCell>
      <TableCell className="tabular-nums">{sku.demand_g > 0 ? `${Math.round(sku.demand_g)} g` : "–"}</TableCell>
      <TableCell className="tabular-nums">{sku.daily_rate_g == null ? "–" : `${sku.daily_rate_g} g/d`}</TableCell>
      <TableCell className="tabular-nums">{days(sku.days_remaining)}</TableCell>
      <TableCell className="tabular-nums">{Math.round(sku.reorder_point_g)} g</TableCell>
      <TableCell>
        <Input
          className="h-8 w-16"
          type="number"
          min={0}
          aria-label={`Lead time for ${skuName(sku)}`}
          value={lead}
          disabled={!canWrite}
          onChange={(e) => setLead(e.target.value)}
          onBlur={() =>
            Number(lead) !== sku.lead_time_days && save.mutate({ ...key, lead_time_days: Math.max(0, Number(lead) || 0) })
          }
        />
      </TableCell>
      <TableCell>
        <StatusBadge sku={sku} />
      </TableCell>
      <TableCell>
        {canWrite && sku.status !== "ok" ? (
          <Button size="sm" variant="ghost" onClick={() => save.mutate({ ...key, alerts_snoozed: !sku.snoozed })}>
            {sku.snoozed ? "Unsnooze" : "Snooze"}
          </Button>
        ) : null}
      </TableCell>
    </TableRow>
  );
}

export function ForecastPanel() {
  const { can } = useAuth();
  const { data, isLoading } = useForecast();
  const canWrite = can("inventory:write");
  if (isLoading || !data) return <Skeleton className="h-40 w-full rounded-lg" />;
  return (
    <div className="space-y-4" data-testid="forecast-panel">
      <ConfigForm data={data} canWrite={canWrite} />
      <Shortfalls data={data} />
      {data.skus.length === 0 ? (
        <p className="text-sm text-muted-foreground">Add spools to see a forecast.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Filament</TableHead>
              <TableHead>In stock</TableHead>
              <TableHead>Queued</TableHead>
              <TableHead>Usage</TableHead>
              <TableHead>Runs out</TableHead>
              <TableHead>Reorder at</TableHead>
              <TableHead>Lead (d)</TableHead>
              <TableHead>Status</TableHead>
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
  const { data, isLoading } = useForecast();
  if (isLoading || !data) return <Skeleton className="h-24 w-full" />;
  if (data.skus.length === 0) return <p className="text-sm text-muted-foreground">No spools yet.</p>;
  const top = data.skus.filter((s) => s.days_remaining != null || s.status !== "ok").slice(0, 6);
  return (
    <div className="space-y-2" data-testid="stock-widget">
      {data.alert_count > 0 ? <p className="text-xs text-muted-foreground">{data.alert_count} need attention</p> : null}
      {top.length === 0 ? (
        <p className="text-sm text-muted-foreground">Not enough usage history yet.</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {top.map((s) => (
            <li key={`${s.material}|${s.brand}|${s.color}`} className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate">{skuName(s)}</span>
              <span className="tabular-nums text-muted-foreground">{days(s.days_remaining)}</span>
              <StatusBadge sku={s} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
