/**
 * Home Assistant settings card (P8 Task 8, Settings → Printer tab, gated
 * `settings:write` same as `PrinterSetupCard`): MQTT publish config (a
 * bridge-status dot + its own connection test) and REST read config (an
 * amber "unreachable" warning when the latest poll failed, or it surfaced
 * per-entity errors), plus the `ha_links` table wiring entities to the
 * dispatcher's hold/pause behavior. Secrets (`ha_mqtt_password`,
 * `ha_token`) are write-only -- `has_mqtt_password`/`has_token` say
 * whether one is stored, but the input never prefills with anything; a
 * blank field on save means "keep the current secret".
 */
import { useEffect, useState } from "react";

import { ApiError } from "@/api/client";
import { useDeleteHaLink, useHaActors, useHaLinks, useHaSettings, useSaveHaSettings, useTestHaMqtt, useTestHaRest } from "@/api/homeAssistant";
import type { HaCondition, HaLink, HaRole, HaSettings, HaSettingsIn } from "@/api/types";
import { HaLinkDialog } from "@/components/settings/HaLinkDialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";

function errText(e: unknown, fallback: string) {
  return e instanceof ApiError ? e.detail : fallback;
}

const ROLE_LABELS: Record<HaRole, string> = {
  energy_kwh: "Energy (kWh)",
  power_w: "Power (W)",
  door: "Door",
  smoke: "Smoke",
  temp: "Temperature",
  humidity: "Humidity",
};

function conditionLabel(cond: HaCondition | null): string {
  if (!cond) return "—";
  const value = Array.isArray(cond.value) ? cond.value.join(", ") : String(cond.value);
  return `${cond.op} ${value}`;
}

function Field({
  id,
  label,
  value,
  onChange,
  type = "text",
  placeholder,
}: {
  id: string;
  label: React.ReactNode;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  placeholder?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} type={type} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} />
    </div>
  );
}

interface MqttDraft {
  ha_mqtt_url: string;
  ha_mqtt_user: string;
  ha_mqtt_password: string;
  ha_discovery_prefix: string;
}

interface RestDraft {
  ha_url: string;
  ha_token: string;
}

const NO_ACTOR = "none";

function seedMqtt(s: HaSettings): MqttDraft {
  return {
    ha_mqtt_url: s.ha_mqtt_url ?? "",
    ha_mqtt_user: s.ha_mqtt_user ?? "",
    ha_mqtt_password: "",
    ha_discovery_prefix: s.ha_discovery_prefix,
  };
}

function seedRest(s: HaSettings): RestDraft {
  return { ha_url: s.ha_url ?? "", ha_token: "" };
}

/** "HA acts as": the service account HA-originated actions (button
 * commands, auto-pause) run as. Saves immediately on change; the bridge
 * reports `actor_ok=false` when it's unset, missing or inactive. */
function ActorField({ data }: { data: HaSettings }) {
  const actors = useHaActors();
  const save = useSaveHaSettings();
  // Computed server-side, so it shows with or without an MQTT bridge.
  const actorBad = !data.actor_ok;
  const actorMsg = data.actor_error ?? "HA commands are blocked";

  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor="ha-actor">HA acts as</Label>
      <div className="flex flex-wrap items-center gap-3">
        <Select
          aria-label="HA acts as"
          value={data.ha_actor_user_id == null ? NO_ACTOR : String(data.ha_actor_user_id)}
          onValueChange={(v) => save.mutate({ ha_actor_user_id: v === NO_ACTOR ? null : Number(v) })}
        >
          <SelectTrigger id="ha-actor" aria-label="HA acts as" className="w-64">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NO_ACTOR}>None</SelectItem>
            {(actors.data ?? []).map((u) => (
              <SelectItem key={u.id} value={String(u.id)}>
                {u.username}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {actorBad ? (
          <span
            role="alert"
            className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-xs text-amber-700 dark:text-amber-400"
          >
            {actorMsg}
          </span>
        ) : null}
      </div>
      <p className="text-xs text-muted-foreground">
        Commands from HA and auto-pause run as this user and need printer control access.
      </p>
      {save.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {errText(save.error, "Could not save the service account.")}
        </p>
      ) : null}
    </div>
  );
}

function MqttSection({
  data,
  draft,
  setDraft,
  onSave,
  saving,
}: {
  data: HaSettings;
  draft: MqttDraft;
  setDraft: (d: MqttDraft) => void;
  onSave: () => void;
  saving: boolean;
}) {
  const test = useTestHaMqtt();
  const bridgeLabel =
    data.bridge_status == null ? "unknown" : data.bridge_status.connected ? "online" : "offline";
  const dotClass =
    bridgeLabel === "online" ? "bg-state-finished" : bridgeLabel === "offline" ? "bg-state-offline" : "bg-state-idle";

  function setField(key: keyof MqttDraft, value: string) {
    setDraft({ ...draft, [key]: value });
  }

  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2">
        <span
          aria-hidden="true"
          data-slot="ha-bridge-dot"
          className={cn("size-2 shrink-0 rounded-full", dotClass)}
        />
        <h3 className="text-sm font-medium" aria-label={`Bridge ${bridgeLabel}`}>
          Publish to HA (MQTT)
        </h3>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field
          id="ha-mqtt-url"
          label="Broker URL"
          value={draft.ha_mqtt_url}
          onChange={(v) => setField("ha_mqtt_url", v)}
          placeholder="mqtt://host:1883"
        />
        <Field id="ha-mqtt-user" label="User" value={draft.ha_mqtt_user} onChange={(v) => setField("ha_mqtt_user", v)} />
        <Field
          id="ha-mqtt-password"
          label={<>Password{data.has_mqtt_password ? <span className="text-muted-foreground"> (set)</span> : null}</>}
          value={draft.ha_mqtt_password}
          onChange={(v) => setField("ha_mqtt_password", v)}
          type="password"
          placeholder={data.has_mqtt_password ? "Leave blank to keep current password" : ""}
        />
        <Field
          id="ha-discovery-prefix"
          label="Discovery prefix"
          value={draft.ha_discovery_prefix}
          onChange={(v) => setField("ha_discovery_prefix", v)}
        />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          variant="outline"
          disabled={test.isPending}
          onClick={() =>
            test.mutate({
              ha_mqtt_url: draft.ha_mqtt_url.trim() || null,
              ha_mqtt_user: draft.ha_mqtt_user.trim() || null,
              ha_mqtt_password: draft.ha_mqtt_password || undefined,
            })
          }
        >
          {test.isPending ? "Testing…" : "Test connection"}
        </Button>
        <Button type="button" disabled={saving} onClick={onSave}>
          {saving ? "Saving…" : "Save"}
        </Button>
        {test.data ? (
          <span
            role={test.data.ok ? undefined : "alert"}
            className={test.data.ok ? "text-sm text-emerald-600 dark:text-emerald-400" : "text-sm text-destructive"}
          >
            {test.data.detail}
          </span>
        ) : null}
      </div>
    </section>
  );
}

function RestSection({
  data,
  draft,
  setDraft,
  onSave,
  saving,
}: {
  data: HaSettings;
  draft: RestDraft;
  setDraft: (d: RestDraft) => void;
  onSave: () => void;
  saving: boolean;
}) {
  const test = useTestHaRest();
  const pullStatus = data.pull_status as { ok?: boolean; errors?: unknown } | null;
  const unreachable = pullStatus != null && pullStatus.ok === false;
  const entityErrors = Array.isArray(pullStatus?.errors) ? (pullStatus.errors as unknown[]) : [];

  function setField(key: keyof RestDraft, value: string) {
    setDraft({ ...draft, [key]: value });
  }

  return (
    <section className="space-y-3">
      <h3 className="text-sm font-medium">Read from HA (REST)</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field
          id="ha-url"
          label="Home Assistant URL"
          value={draft.ha_url}
          onChange={(v) => setField("ha_url", v)}
          placeholder="http://homeassistant.local:8123"
        />
        <Field
          id="ha-token"
          label={<>Token{data.has_token ? <span className="text-muted-foreground"> (set)</span> : null}</>}
          value={draft.ha_token}
          onChange={(v) => setField("ha_token", v)}
          type="password"
          placeholder={data.has_token ? "Leave blank to keep current token" : ""}
        />
      </div>
      <ActorField data={data} />
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant="outline" disabled={test.isPending} onClick={() => test.mutate()}>
          {test.isPending ? "Testing…" : "Test connection"}
        </Button>
        <Button type="button" disabled={saving} onClick={onSave}>
          {saving ? "Saving…" : "Save"}
        </Button>
        {test.data ? (
          <span
            role={test.data.ok ? undefined : "alert"}
            className={test.data.ok ? "text-sm text-emerald-600 dark:text-emerald-400" : "text-sm text-destructive"}
          >
            {test.data.detail}
          </span>
        ) : null}
      </div>
      {unreachable ? (
        <p
          role="alert"
          className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400"
        >
          Home Assistant unreachable, queue holds are ignored
        </p>
      ) : null}
      {entityErrors.length > 0 ? (
        <p role="alert" className="text-sm text-amber-700 dark:text-amber-400">
          {entityErrors.map((e) => String(e)).join(", ")}
        </p>
      ) : null}
    </section>
  );
}

function LinksSection() {
  const links = useHaLinks();
  const del = useDeleteHaLink();
  const [editing, setEditing] = useState<HaLink | "new" | null>(null);

  return (
    <section className="space-y-3">
      <h3 className="text-sm font-medium">Linked entities</h3>
      {links.isLoading ? (
        <Skeleton className="h-16 w-full rounded-lg" />
      ) : links.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {errText(links.error, "Couldn't load linked entities.")}
        </p>
      ) : (links.data ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">No entities linked yet.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Entity</TableHead>
              <TableHead>Printer</TableHead>
              <TableHead>Role</TableHead>
              <TableHead>Condition</TableHead>
              <TableHead>Hold queue</TableHead>
              <TableHead>Pause print</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {(links.data ?? []).map((link) => (
              <TableRow key={link.id}>
                <TableCell className="font-mono text-xs">{link.entity_id}</TableCell>
                <TableCell>{link.printer_name ?? "All"}</TableCell>
                <TableCell>{ROLE_LABELS[link.role]}</TableCell>
                <TableCell>{conditionLabel(link.alert_condition)}</TableCell>
                <TableCell>
                  <Switch checked={link.hold_queue} disabled aria-label={`Hold queue for ${link.entity_id}`} />
                </TableCell>
                <TableCell>
                  <Switch checked={link.pause_print} disabled aria-label={`Pause print for ${link.entity_id}`} />
                </TableCell>
                <TableCell>
                  <div className="flex gap-1">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      aria-label={`Edit ${link.entity_id}`}
                      onClick={() => setEditing(link)}
                    >
                      Edit
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="destructive"
                      aria-label={`Delete ${link.entity_id}`}
                      disabled={del.isPending}
                      onClick={() => del.mutate(link.id)}
                    >
                      Delete
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <p className="text-xs text-muted-foreground">
        Pause print is off by default — it pauses a running print when the alert fires.
      </p>
      {del.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {errText(del.error, "Could not delete the link.")}
        </p>
      ) : null}
      <Button type="button" variant="outline" onClick={() => setEditing("new")}>
        Add link
      </Button>
      <HaLinkDialog
        open={editing !== null}
        link={editing !== "new" ? editing : null}
        onOpenChange={(open) => {
          if (!open) setEditing(null);
        }}
      />
    </section>
  );
}

export function HomeAssistantCard() {
  const settings = useHaSettings();
  const save = useSaveHaSettings();
  const [mqttDraft, setMqttDraft] = useState<MqttDraft | null>(null);
  const [restDraft, setRestDraft] = useState<RestDraft | null>(null);

  useEffect(() => {
    if (settings.data && mqttDraft === null) setMqttDraft(seedMqtt(settings.data));
    if (settings.data && restDraft === null) setRestDraft(seedRest(settings.data));
  }, [settings.data, mqttDraft, restDraft]);

  if (settings.isLoading || mqttDraft === null || restDraft === null) {
    return <Skeleton className="h-96 w-full rounded-xl" />;
  }

  if (settings.isError || !settings.data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Home Assistant</CardTitle>
          <CardDescription>{errText(settings.error, "Couldn't load these settings.")}</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const data = settings.data;

  function saveMqtt() {
    if (!mqttDraft) return;
    const body: HaSettingsIn = {
      ha_mqtt_url: mqttDraft.ha_mqtt_url.trim() || null,
      ha_mqtt_user: mqttDraft.ha_mqtt_user.trim() || null,
      ha_discovery_prefix: mqttDraft.ha_discovery_prefix.trim() || null,
    };
    if (mqttDraft.ha_mqtt_password) body.ha_mqtt_password = mqttDraft.ha_mqtt_password;
    save.mutate(body);
  }

  function saveRest() {
    if (!restDraft) return;
    const body: HaSettingsIn = { ha_url: restDraft.ha_url.trim() || null };
    if (restDraft.ha_token) body.ha_token = restDraft.ha_token;
    save.mutate(body);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Home Assistant</CardTitle>
        <CardDescription>
          Publish printer state to HA over MQTT, and read HA entities (doors, smoke, energy
          meters) to hold the queue or pause a running print.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <MqttSection data={data} draft={mqttDraft} setDraft={setMqttDraft} onSave={saveMqtt} saving={save.isPending} />
        <RestSection data={data} draft={restDraft} setDraft={setRestDraft} onSave={saveRest} saving={save.isPending} />
        {save.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {errText(save.error, "Could not save these settings.")}
          </p>
        ) : null}
        <LinksSection />
      </CardContent>
    </Card>
  );
}
