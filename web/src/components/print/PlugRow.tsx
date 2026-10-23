import { PlugIcon } from "lucide-react";

import { usePlug, usePlugToggle } from "@/api/ops";
import { Switch } from "@/components/ui/switch";

/** Smart-plug state + toggle for a printer; renders nothing when no plug is configured.
 * Exported for Q3's CardFooter. */
export function PlugRow({ printerId, canControl = true }: { printerId: number; canControl?: boolean }) {
  const plug = usePlug(printerId);
  const toggle = usePlugToggle(printerId);
  const p = plug.data;
  if (!p) return null;
  const on = p.last_state === "on";
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm" data-testid="plug-row">
      <PlugIcon className="size-4 text-muted-foreground" />
      <span className="font-medium">{p.last_state ? (on ? "On" : "Off") : "Unknown"}</span>
      {p.last_power_w != null ? <span className="text-muted-foreground">{p.last_power_w.toFixed(0)} W</span> : null}
      {p.auto_off_after_min ? (
        <span className="text-xs text-muted-foreground">auto-off {p.auto_off_after_min} min after print</span>
      ) : null}
      <Switch
        className="ml-auto"
        aria-label="Smart plug power"
        checked={on}
        disabled={!canControl || toggle.isPending}
        onCheckedChange={(v) => toggle.mutate(v)}
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
