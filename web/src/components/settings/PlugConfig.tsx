import { useState } from "react";

import { useAuth } from "@/api/auth";
import { usePrinterPlugs } from "@/api/ops";
import { usePrinters, useUpdatePrinter } from "@/api/printers";
import { PlugForm } from "@/components/plugs/PlugForm";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

/** A printer's linked smart plugs (many allowed) plus the "power off when the
 * queue drains" switch. Needs `settings:write`; hidden otherwise. Plugs are
 * standalone records -- this section is a filtered view with an "add" shortcut. */
export function PlugConfig({ printerId }: { printerId: number }) {
  const auth = useAuth();
  const allowed = auth.can("settings:write");
  const plugs = usePrinterPlugs(printerId, false);
  const printers = usePrinters();
  const update = useUpdatePrinter(printerId);
  const [editing, setEditing] = useState<number | "new" | null>(null);

  if (!allowed) return null;
  const printer = printers.data?.find((p) => p.id === printerId);
  const list = plugs.data ?? [];

  return (
    <div className="space-y-3 border-t pt-3" data-testid={`plug-config-${printerId}`}>
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-foreground">Smart plugs</h3>
        {editing === null ? (
          <Button type="button" size="sm" variant="outline" onClick={() => setEditing("new")}>
            Add plug
          </Button>
        ) : null}
      </div>
      {list.length === 0 && editing === null ? (
        <p className="text-sm text-muted-foreground">No plugs linked to this printer.</p>
      ) : null}
      {list.map((p) =>
        editing === p.id ? (
          <PlugForm key={p.id} plug={p} onDone={() => setEditing(null)} />
        ) : (
          <div key={p.id} className="flex items-center gap-3 text-sm">
            <span className="font-medium">{p.name || `Plug ${p.id}`}</span>
            <span className="text-muted-foreground">
              {p.kind}
              {p.controls_printer_power ? " - printer power" : " - accessory"}
            </span>
            <Button type="button" size="sm" variant="ghost" className="ml-auto" onClick={() => setEditing(p.id)}>
              Edit
            </Button>
          </div>
        ),
      )}
      {editing === "new" ? <PlugForm printerId={printerId} onDone={() => setEditing(null)} /> : null}
      {list.some((p) => p.controls_printer_power) && printer ? (
        <div className="flex items-center gap-3">
          <Switch
            id={`drain-off-${printerId}`}
            checked={Boolean(printer.power_off_when_drained)}
            disabled={update.isPending}
            onCheckedChange={(v) => update.mutate({ power_off_when_drained: v })}
          />
          <Label htmlFor={`drain-off-${printerId}`}>Power off the printer when the queue drains</Label>
        </div>
      ) : null}
    </div>
  );
}
