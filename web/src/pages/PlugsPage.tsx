/** `/print/plugs`: every smart plug (standalone or linked) with live state,
 * power draw, accumulated energy, a toggle, and add/edit for `settings:write`. */
import { PlugIcon } from "lucide-react";
import { useState } from "react";

import { useAuth } from "@/api/auth";
import { usePlugs, usePlugToggle } from "@/api/ops";
import { usePrinters } from "@/api/printers";
import { PlugForm } from "@/components/plugs/PlugForm";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { PageContainer } from "@/components/ui/page-container";
import { Switch } from "@/components/ui/switch";

export function PlugsPage() {
  const auth = useAuth();
  const canEdit = auth.can("settings:write");
  const canControl = auth.can("printers:control");
  const plugs = usePlugs();
  const printers = usePrinters();
  const toggle = usePlugToggle();
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const list = plugs.data ?? [];
  const printerName = (id: number | null) => printers.data?.find((p) => p.id === id)?.name;
  const totalW = list.reduce((a, p) => a + (p.last_power_w ?? 0), 0);
  const totalKwh = list.reduce((a, p) => a + p.energy_kwh, 0);

  return (
    <PageContainer width="default">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-lg font-semibold text-foreground">Smart plugs</h1>
          <p className="text-sm text-muted-foreground">
            {list.length} plug{list.length === 1 ? "" : "s"} - {totalW.toFixed(0)} W now - {totalKwh.toFixed(2)} kWh total
          </p>
        </div>
        {canEdit && editing === null ? (
          <Button type="button" size="sm" onClick={() => setEditing("new")}>
            Add plug
          </Button>
        ) : null}
      </div>
      {editing === "new" ? (
        <Card>
          <CardContent className="pt-4">
            <PlugForm onDone={() => setEditing(null)} />
          </CardContent>
        </Card>
      ) : null}
      {list.length === 0 && editing === null ? <p className="text-sm text-muted-foreground">No smart plugs yet.</p> : null}
      {list.map((p) => {
        const on = p.last_state === "on";
        const label = p.name || `Plug ${p.id}`;
        return (
          <Card key={p.id} data-testid="plug-card">
            <CardContent className="space-y-3 pt-4">
              {editing === p.id ? (
                <PlugForm plug={p} onDone={() => setEditing(null)} />
              ) : (
                <div className="flex flex-wrap items-center gap-3 text-sm">
                  <PlugIcon className="size-4 text-muted-foreground" />
                  <span className="font-medium">{label}</span>
                  <span className="text-muted-foreground">
                    {p.kind}
                    {p.printer_id != null
                      ? ` - ${printerName(p.printer_id) ?? `printer ${p.printer_id}`}${p.controls_printer_power ? "" : " (accessory)"}`
                      : " - standalone"}
                  </span>
                  <span className="font-medium">{p.last_state ? (on ? "On" : "Off") : "Unknown"}</span>
                  <span className="text-muted-foreground">
                    {p.last_power_w != null ? `${p.last_power_w.toFixed(0)} W - ` : ""}
                    {p.energy_kwh.toFixed(2)} kWh
                  </span>
                  <div className="ml-auto flex items-center gap-2">
                    {canEdit ? (
                      <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(p.id)}>
                        Edit
                      </Button>
                    ) : null}
                    <Switch
                      aria-label={`${label} power`}
                      checked={on}
                      disabled={!canControl || toggle.isPending}
                      onCheckedChange={(v) => toggle.mutate({ id: p.id, on: v })}
                    />
                  </div>
                  {p.last_error ? (
                    <p role="alert" className="w-full text-xs text-destructive">
                      {p.last_error}
                    </p>
                  ) : null}
                </div>
              )}
            </CardContent>
          </Card>
        );
      })}
    </PageContainer>
  );
}
