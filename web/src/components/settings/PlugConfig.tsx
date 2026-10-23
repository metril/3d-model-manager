import { useEffect, useRef, useState } from "react";

import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useDeletePlug, usePlug, useSavePlug } from "@/api/ops";
import type { PlugKind } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

const NONE = "none";
const FIELDS: Record<PlugKind, { key: string; label: string; secret?: boolean }[]> = {
  tasmota: [
    { key: "host", label: "Host" },
    { key: "user", label: "User" },
    { key: "password", label: "Password", secret: true },
  ],
  ha: [
    { key: "entity_id", label: "Switch entity ID" },
    { key: "power_entity_id", label: "Power sensor entity ID" },
  ],
  mqtt: [
    { key: "command_topic", label: "Command topic" },
    { key: "on_payload", label: "On payload" },
    { key: "off_payload", label: "Off payload" },
  ],
  rest: [
    { key: "on_url", label: "On URL" },
    { key: "off_url", label: "Off URL" },
    { key: "status_url", label: "Status URL" },
    { key: "state_path", label: "State path" },
    { key: "power_path", label: "Power path" },
    { key: "headers", label: "Headers (JSON)", secret: true },
  ],
};

/** Per-printer smart-plug configuration. Needs `settings:write`; hidden otherwise.
 * "Save" validates server-side (422 on a bad config), so there is no separate test call. */
export function PlugConfig({ printerId }: { printerId: number }) {
  const auth = useAuth();
  const allowed = auth.can("settings:write");
  const plug = usePlug(printerId, false);
  const save = useSavePlug(printerId);
  const del = useDeletePlug(printerId);
  const [kind, setKind] = useState<string>(NONE);
  const [cfg, setCfg] = useState<Record<string, string>>({});
  const [autoOff, setAutoOff] = useState("");
  const [autoOn, setAutoOn] = useState(false);

  const p = plug.data;
  const filled = useRef<number | null>(null);
  useEffect(() => {
    if (!p || filled.current === printerId) return;
    filled.current = printerId;
    setKind(p.kind);
    setCfg(
      Object.fromEntries(
        Object.entries(p.config ?? {}).map(([k, v]) => [k, typeof v === "string" ? v : JSON.stringify(v)]),
      ),
    );
    setAutoOff(p.auto_off_after_min != null ? String(p.auto_off_after_min) : "");
    setAutoOn(p.auto_on_before_print);
  }, [p, printerId]);

  if (!allowed) return null;
  const fields = kind === NONE ? [] : FIELDS[kind as PlugKind];

  function onSave() {
    const config: Record<string, unknown> = {};
    for (const f of fields) {
      const v = (cfg[f.key] ?? "").trim();
      if (!v) continue;
      if (f.key === "headers") {
        try {
          config[f.key] = v === "***" ? v : JSON.parse(v);
        } catch {
          config[f.key] = v;
        }
      } else config[f.key] = v;
    }
    save.mutate({
      kind: kind as PlugKind,
      config,
      auto_off_after_min: autoOff.trim() ? Number(autoOff) : null,
      auto_on_before_print: autoOn,
    });
  }

  return (
    <div className="space-y-3 border-t pt-3" data-testid={`plug-config-${printerId}`}>
      <h3 className="text-sm font-semibold text-foreground">Smart plug</h3>
      <div className="space-y-1.5">
        <Label htmlFor={`plug-kind-${printerId}`}>Plug type</Label>
        <Select value={kind} onValueChange={setKind}>
          <SelectTrigger id={`plug-kind-${printerId}`} aria-label="Plug type">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NONE}>None</SelectItem>
            <SelectItem value="tasmota">Tasmota</SelectItem>
            <SelectItem value="ha">Home Assistant</SelectItem>
            <SelectItem value="mqtt">MQTT</SelectItem>
            <SelectItem value="rest">REST</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {fields.length ? (
        <div className="grid gap-3 sm:grid-cols-2">
          {fields.map((f) => (
            <div key={f.key} className="space-y-1.5">
              <Label htmlFor={`plug-${printerId}-${f.key}`}>{f.label}</Label>
              <Input
                id={`plug-${printerId}-${f.key}`}
                type={f.secret && f.key === "password" ? "password" : "text"}
                value={cfg[f.key] ?? ""}
                onChange={(e) => setCfg((c) => ({ ...c, [f.key]: e.target.value }))}
              />
            </div>
          ))}
          <div className="space-y-1.5">
            <Label htmlFor={`plug-${printerId}-autooff`}>Auto-off after print (minutes)</Label>
            <Input
              id={`plug-${printerId}-autooff`}
              type="number"
              min="1"
              value={autoOff}
              onChange={(e) => setAutoOff(e.target.value)}
            />
          </div>
          <div className="flex items-center gap-3">
            <Switch id={`plug-${printerId}-autoon`} checked={autoOn} onCheckedChange={setAutoOn} />
            <Label htmlFor={`plug-${printerId}-autoon`}>Power on before print</Label>
          </div>
        </div>
      ) : null}
      <div className="flex gap-2">
        {kind !== NONE ? (
          <Button type="button" size="sm" onClick={onSave} disabled={save.isPending}>
            {save.isPending ? "Saving..." : "Save plug"}
          </Button>
        ) : null}
        {p ? (
          <Button
            type="button"
            size="sm"
            variant="destructive"
            disabled={del.isPending}
            onClick={() =>
              del.mutate(undefined, {
                onSuccess: () => {
                  setKind(NONE);
                  setCfg({});
                },
              })
            }
          >
            Remove plug
          </Button>
        ) : null}
      </div>
      {save.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {save.error instanceof ApiError ? save.error.detail : "Could not save plug"}
        </p>
      ) : null}
      {save.isSuccess ? <p className="text-sm text-emerald-600">Plug saved and verified.</p> : null}
    </div>
  );
}
