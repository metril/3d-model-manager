import { useState } from "react";

import { ApiError } from "@/api/client";
import { usePrinters } from "@/api/printers";
import { useDeletePlug, useSavePlug } from "@/api/ops";
import type { PlugDelayMode, PlugIn, PlugKind, PlugOut } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

const NO_PRINTER = "none";
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

const numOrNull = (v: string) => (v.trim() ? Number(v) : null);

function Toggle({ id, label, checked, onChange }: { id: string; label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-center gap-3">
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
      <Label htmlFor={id}>{label}</Label>
    </div>
  );
}

function Field({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
    </div>
  );
}

/** Create / edit one smart plug. "Save" validates server-side (422 on a bad
 * config), so there is no separate test call. `printerId` pre-links a new plug. */
export function PlugForm({
  plug,
  printerId = null,
  onDone,
}: {
  plug?: PlugOut;
  printerId?: number | null;
  onDone: () => void;
}) {
  const save = useSavePlug();
  const del = useDeletePlug();
  const printers = usePrinters();
  const key = plug ? `plug-${plug.id}` : "plug-new";

  const [name, setName] = useState(plug?.name ?? "");
  const [linked, setLinked] = useState<string>(String(plug?.printer_id ?? printerId ?? NO_PRINTER));
  const [controls, setControls] = useState(plug?.controls_printer_power ?? true);
  const [kind, setKind] = useState<PlugKind>(plug?.kind ?? "tasmota");
  const [cfg, setCfg] = useState<Record<string, string>>(
    Object.fromEntries(
      Object.entries(plug?.config ?? {}).map(([k, v]) => [k, typeof v === "string" ? v : JSON.stringify(v)]),
    ),
  );
  const [autoOn, setAutoOn] = useState(plug?.auto_on_before_print ?? false);
  const [autoOff, setAutoOff] = useState(plug?.auto_off ?? false);
  const [mode, setMode] = useState<PlugDelayMode>(plug?.off_delay_mode ?? "time");
  const [minutes, setMinutes] = useState(plug?.auto_off_after_min != null ? String(plug.auto_off_after_min) : "5");
  const [threshold, setThreshold] = useState(String(plug?.off_temp_threshold ?? 40));
  const [dryOff, setDryOff] = useState(plug?.auto_off_after_drying ?? false);
  const [dryDelay, setDryDelay] = useState(String(plug?.drying_off_delay_min ?? 0));
  const [alertOn, setAlertOn] = useState(plug?.power_alert_enabled ?? false);
  const [alertHigh, setAlertHigh] = useState(plug?.power_alert_high_w != null ? String(plug.power_alert_high_w) : "");
  const [alertLow, setAlertLow] = useState(plug?.power_alert_low_w != null ? String(plug.power_alert_low_w) : "");
  const [schedOn, setSchedOn] = useState(plug?.schedule_enabled ?? false);
  const [onTime, setOnTime] = useState(plug?.schedule_on_time ?? "");
  const [offTime, setOffTime] = useState(plug?.schedule_off_time ?? "");
  const [tz, setTz] = useState(plug?.schedule_tz ?? "UTC");
  const [bar, setBar] = useState(plug?.show_in_switchbar ?? false);

  const fields = FIELDS[kind];
  const hasPrinter = linked !== NO_PRINTER;

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
    const body: PlugIn = {
      name: name.trim(),
      printer_id: hasPrinter ? Number(linked) : null,
      controls_printer_power: controls,
      kind,
      config,
      auto_off: autoOff,
      auto_off_after_min: numOrNull(minutes),
      off_delay_mode: mode,
      off_temp_threshold: Number(threshold) || 40,
      auto_on_before_print: autoOn,
      auto_off_after_drying: dryOff,
      drying_off_delay_min: Number(dryDelay) || 0,
      power_alert_enabled: alertOn,
      power_alert_high_w: numOrNull(alertHigh),
      power_alert_low_w: numOrNull(alertLow),
      schedule_enabled: schedOn,
      schedule_on_time: onTime || null,
      schedule_off_time: offTime || null,
      schedule_tz: tz.trim() || "UTC",
      show_in_switchbar: bar,
    };
    save.mutate({ id: plug?.id, body }, { onSuccess: onDone });
  }

  return (
    <div className="space-y-4" data-testid={`${key}-form`}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field id={`${key}-name`} label="Name">
          <Input id={`${key}-name`} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field id={`${key}-kind`} label="Plug type">
          <Select value={kind} onValueChange={(v) => setKind(v as PlugKind)}>
            <SelectTrigger id={`${key}-kind`} aria-label="Plug type">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="tasmota">Tasmota</SelectItem>
              <SelectItem value="ha">Home Assistant</SelectItem>
              <SelectItem value="mqtt">MQTT</SelectItem>
              <SelectItem value="rest">REST</SelectItem>
            </SelectContent>
          </Select>
        </Field>
        {fields.map((f) => (
          <Field key={f.key} id={`${key}-${f.key}`} label={f.label}>
            <Input
              id={`${key}-${f.key}`}
              type={f.secret && f.key === "password" ? "password" : "text"}
              value={cfg[f.key] ?? ""}
              onChange={(e) => setCfg((c) => ({ ...c, [f.key]: e.target.value }))}
            />
          </Field>
        ))}
        <Field id={`${key}-printer`} label="Linked printer (optional)">
          <Select value={linked} onValueChange={setLinked}>
            <SelectTrigger id={`${key}-printer`} aria-label="Linked printer">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_PRINTER}>None (standalone)</SelectItem>
              {(printers.data ?? []).map((p) => (
                <SelectItem key={p.id} value={String(p.id)}>
                  {p.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
      </div>

      <div className="space-y-3">
        <Toggle id={`${key}-bar`} label="Show in the quick-toggle switchbar" checked={bar} onChange={setBar} />
        {hasPrinter ? (
          <>
            <Toggle id={`${key}-controls`} label="Cuts the printer's own power" checked={controls} onChange={setControls} />
            <Toggle id={`${key}-autoon`} label="Power on before a queued print" checked={autoOn} onChange={setAutoOn} />
            <Toggle id={`${key}-autooff`} label="Auto-off after print" checked={autoOff} onChange={setAutoOff} />
            {autoOff ? (
              <div className="grid gap-3 pl-12 sm:grid-cols-3">
                <Field id={`${key}-mode`} label="Wait for">
                  <Select value={mode} onValueChange={(v) => setMode(v as PlugDelayMode)}>
                    <SelectTrigger id={`${key}-mode`} aria-label="Auto-off mode">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="time">A delay</SelectItem>
                      <SelectItem value="temperature">Nozzle and bed to cool</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>
                {mode === "time" ? (
                  <Field id={`${key}-minutes`} label="Auto-off after print (minutes)">
                    <Input id={`${key}-minutes`} type="number" min="1" value={minutes} onChange={(e) => setMinutes(e.target.value)} />
                  </Field>
                ) : (
                  <Field id={`${key}-threshold`} label="Cool-down threshold (C)">
                    <Input id={`${key}-threshold`} type="number" min="20" value={threshold} onChange={(e) => setThreshold(e.target.value)} />
                  </Field>
                )}
              </div>
            ) : null}
            <Toggle id={`${key}-dry`} label="Auto-off after AMS drying" checked={dryOff} onChange={setDryOff} />
            {dryOff ? (
              <div className="pl-12">
                <Field id={`${key}-drydelay`} label="Drying cool-down (minutes)">
                  <Input id={`${key}-drydelay`} type="number" min="0" className="max-w-40" value={dryDelay} onChange={(e) => setDryDelay(e.target.value)} />
                </Field>
              </div>
            ) : null}
          </>
        ) : null}
        <Toggle id={`${key}-alert`} label="Power alerts" checked={alertOn} onChange={setAlertOn} />
        {alertOn ? (
          <div className="grid gap-3 pl-12 sm:grid-cols-2">
            <Field id={`${key}-high`} label="Alert above (W)">
              <Input id={`${key}-high`} type="number" min="0" value={alertHigh} onChange={(e) => setAlertHigh(e.target.value)} />
            </Field>
            <Field id={`${key}-low`} label="Alert below (W, while on)">
              <Input id={`${key}-low`} type="number" min="0" value={alertLow} onChange={(e) => setAlertLow(e.target.value)} />
            </Field>
          </div>
        ) : null}
        <Toggle id={`${key}-sched`} label="Daily schedule" checked={schedOn} onChange={setSchedOn} />
        {schedOn ? (
          <div className="grid gap-3 pl-12 sm:grid-cols-3">
            <Field id={`${key}-ontime`} label="Turn on at">
              <Input id={`${key}-ontime`} type="time" value={onTime} onChange={(e) => setOnTime(e.target.value)} />
            </Field>
            <Field id={`${key}-offtime`} label="Turn off at">
              <Input id={`${key}-offtime`} type="time" value={offTime} onChange={(e) => setOffTime(e.target.value)} />
            </Field>
            <Field id={`${key}-tz`} label="Timezone">
              <Input id={`${key}-tz`} value={tz} onChange={(e) => setTz(e.target.value)} />
            </Field>
          </div>
        ) : null}
      </div>

      <div className="flex gap-2">
        <Button type="button" size="sm" onClick={onSave} disabled={save.isPending}>
          {save.isPending ? "Saving..." : "Save plug"}
        </Button>
        {plug ? (
          <Button
            type="button"
            size="sm"
            variant="destructive"
            disabled={del.isPending}
            onClick={() => del.mutate(plug.id, { onSuccess: onDone })}
          >
            Remove plug
          </Button>
        ) : null}
        <Button type="button" size="sm" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
      {save.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {save.error instanceof ApiError ? save.error.detail : "Could not save plug"}
        </p>
      ) : null}
    </div>
  );
}
