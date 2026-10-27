import { PlugIcon, PowerIcon } from "lucide-react";

import { usePlugSummary, usePlugToggle, usePrinterPlugs } from "@/api/ops";
import type { PlugOut } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";

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
  return <PlugRowView plug={summary.data?.plugs?.[String(printerId)]} canControl={canControl} />;
}

function PlugRowView({ plug: p, canControl }: { plug: PlugOut | null | undefined; canControl: boolean }) {
  const toggle = usePlugToggle();
  if (!p) return null;
  const on = p.last_state === "on";
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm" data-testid="plug-row">
      <PlugIcon className="size-4 text-muted-foreground" />
      <span className="font-medium">{p.last_state ? (on ? "On" : "Off") : "Unknown"}</span>
      {p.last_power_w != null ? <span className="text-muted-foreground">{p.last_power_w.toFixed(0)} W</span> : null}
      {p.auto_off && p.off_delay_mode === "time" && p.auto_off_after_min ? (
        <span className="text-xs text-muted-foreground">auto-off {p.auto_off_after_min} min after print</span>
      ) : null}
      {p.auto_off && p.off_delay_mode === "temperature" ? (
        <span className="text-xs text-muted-foreground">auto-off when below {p.off_temp_threshold} C</span>
      ) : null}
      <Switch
        className="ml-auto"
        aria-label="Smart plug power"
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
          Could not switch the plug.
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
