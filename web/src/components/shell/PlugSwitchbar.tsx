import { PlugIcon } from "lucide-react";

import { useFeatures } from "@/api/features";
import { usePlugs, usePlugToggle } from "@/api/ops";
import { Switch } from "@/components/ui/switch";

/** Quick-toggle strip in the app shell: every plug flagged "show in switchbar".
 * Renders nothing when there are none (or the printer feature is off). */
export function PlugSwitchbar() {
  const features = useFeatures();
  const plugs = usePlugs({ enabled: Boolean(features.data?.printer_enabled) });
  const toggle = usePlugToggle();
  const bar = (plugs.data ?? []).filter((p) => p.show_in_switchbar);
  if (bar.length === 0) return null;
  return (
    <div className="flex items-center gap-3" data-testid="plug-switchbar">
      {bar.map((p) => {
        const on = p.last_state === "on";
        const label = p.name || `Plug ${p.id}`;
        return (
          <label key={p.id} className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <PlugIcon className="size-3.5" />
            <span className="max-w-24 truncate">{label}</span>
            <Switch
              aria-label={`${label} power`}
              checked={on}
              disabled={toggle.isPending}
              onCheckedChange={(v) => toggle.mutate({ id: p.id, on: v })}
            />
          </label>
        );
      })}
    </div>
  );
}
