import { PlugIcon, PowerIcon, ZapIcon } from "lucide-react";

import { usePlugSummary, usePlugToggle, usePrinterPlugs } from "@/api/ops";
import type { PlugOut } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useTranslation } from "react-i18next";

/** The printer's main smart plug (state + toggle); renders nothing when none is configured.
 * Exported for Q3's CardFooter. */
export function PlugRow({ printerId, canControl = true }: { printerId: number; canControl?: boolean }) {
  const plugs = usePrinterPlugs(printerId);
  const list = plugs.data ?? [];
  const main = list.find((p) => p.controls_printer_power) ?? list[0];
  return <PlugRowView plug={main} canControl={canControl} />;
}

/** Card variant: reads the shared summary poll instead of one query per printer. */
export function SharedPlugRow({ printerId, canControl = true }: { printerId: number; canControl?: boolean }) {
  const summary = usePlugSummary();
  return (
    <PlugRowView
      plug={summary.data?.plugs?.[String(printerId)]}
      linkPowerW={summary.data?.link_power_w?.[String(printerId)]}
      canControl={canControl}
    />
  );
}

function PlugRowView({
  plug: p,
  canControl,
  linkPowerW,
}: {
  plug: PlugOut | null | undefined;
  canControl: boolean;
  /** Live watts from an HA `power_w` link; shown when the plug reports none. */
  linkPowerW?: number;
}) {
  const { t } = useTranslation();
  const toggle = usePlugToggle();
  if (!p) {
    if (linkPowerW == null) return null;
    return (
      <div className="flex items-center gap-2 text-sm" data-testid="link-power-row">
        <ZapIcon className="size-4 text-muted-foreground" />
        <span className="text-muted-foreground">{linkPowerW.toFixed(0)} W</span>
      </div>
    );
  }
  const watts = p.last_power_w ?? linkPowerW ?? null;
  const on = p.last_state === "on";
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm" data-testid="plug-row">
      <PlugIcon className="size-4 text-muted-foreground" />
      <span className="font-medium">{p.last_state ? (on ? t("plugRow.on") : t("plugRow.off")) : t("plugRow.unknown")}</span>
      {watts != null ? <span className="text-muted-foreground">{watts.toFixed(0)} W</span> : null}
      {p.auto_off && p.off_delay_mode === "time" && p.auto_off_after_min ? (
        <span className="text-xs text-muted-foreground">{t("plugRow.autoOffTime", { min: p.auto_off_after_min })}</span>
      ) : null}
      {p.auto_off && p.off_delay_mode === "temperature" ? (
        <span className="text-xs text-muted-foreground">{t("plugRow.autoOffTemp", { temp: p.off_temp_threshold })}</span>
      ) : null}
      <Switch
        className="ml-auto"
        aria-label={t("plugRow.power")}
        checked={on}
        disabled={!canControl || toggle.isPending}
        onCheckedChange={(v) => toggle.mutate({ id: p.id, on: v })}
      />
      {p.last_error ? (
        <p role="alert" className="w-full text-xs text-destructive">
          {p.last_error}
        </p>
      ) : null}
      {toggle.isError ? (
        <p role="alert" className="w-full text-xs text-destructive">
          {t("plugRow.switchFailed")}
        </p>
      ) : null}
    </div>
  );
}

/** "Power on" for an offline printer: only when a smart plug is linked and not already on. */
export function SharedPlugPowerOn({ printerId, canControl = true, label }: { printerId: number; canControl?: boolean; label: string }) {
  const summary = usePlugSummary();
  const toggle = usePlugToggle();
  const plug = summary.data?.plugs?.[String(printerId)];
  if (!plug || !canControl || plug.last_state === "on") return null;
  return (
    <Button type="button" size="sm" variant="secondary" disabled={toggle.isPending} onClick={() => toggle.mutate({ id: plug.id, on: true })}>
      <PowerIcon />
      {label}
    </Button>
  );
}
