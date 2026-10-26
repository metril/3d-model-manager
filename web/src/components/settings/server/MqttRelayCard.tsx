import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { useRelay, useRelayActors, useSaveRelay, useTestRelay, type MqttRelay, type MqttRelayIn } from "@/api/adminLogs";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

const MASK = "***";
const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";

function Form({ data }: { data: MqttRelay }) {
  const { t } = useTranslation();
  const save = useSaveRelay();
  const test = useTestRelay();
  const actors = useRelayActors();
  const [enabled, setEnabled] = useState(data.enabled);
  const [host, setHost] = useState(data.host ?? "");
  const [port, setPort] = useState(String(data.port));
  const [tls, setTls] = useState(data.tls);
  const [tlsVerify, setTlsVerify] = useState(data.tls_verify);
  const [username, setUsername] = useState(data.username ?? "");
  const [password, setPassword] = useState(data.has_password ? MASK : "");
  const [prefix, setPrefix] = useState(data.topic_prefix);
  const [commands, setCommands] = useState(data.commands_enabled);
  const [actor, setActor] = useState(data.actor_user_id != null ? String(data.actor_user_id) : "");

  useEffect(() => {
    if (!tls) setTlsVerify(true);
  }, [tls]);

  const portN = Number(port);
  const valid =
    Number.isInteger(portN) && portN >= 1 && portN <= 65535 && /^[A-Za-z0-9_.-]+(\/[A-Za-z0-9_.-]+)*$/.test(prefix) && (!enabled || host.trim() !== "") && (!commands || actor !== "");
  const body = (): MqttRelayIn => ({
    enabled,
    host: host.trim() || null,
    port: portN,
    tls,
    tls_verify: tlsVerify,
    username: username.trim() || null,
    password,
    topic_prefix: prefix,
    commands_enabled: commands,
    actor_user_id: actor ? Number(actor) : null,
  });
  const st = data.status;
  const statusText = !data.enabled
    ? t("settings.server.relay.statusOff")
    : !st
      ? t("settings.server.relay.statusNone")
      : st.connected
        ? t("settings.server.relay.statusOk", { n: st.published })
        : t("settings.server.relay.statusDown", { error: st.error ?? "" });

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settings.server.relay.title")}</CardTitle>
        <CardDescription>{t("settings.server.relay.desc")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <p role="status" data-testid="relay-status" className="text-sm text-muted-foreground">
          {statusText}
        </p>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          {t("settings.server.relay.enable")}
        </label>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="relay-host">{t("settings.server.relay.host")}</Label>
            <Input id="relay-host" value={host} onChange={(e) => setHost(e.target.value)} placeholder="mqtt.example.com" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="relay-port">{t("settings.server.relay.port")}</Label>
            <Input id="relay-port" type="number" min={1} max={65535} value={port} onChange={(e) => setPort(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="relay-user">{t("settings.server.relay.username")}</Label>
            <Input id="relay-user" autoComplete="off" value={username} onChange={(e) => setUsername(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="relay-pass">{t("settings.server.relay.password")}</Label>
            <Input
              id="relay-pass"
              type="password"
              autoComplete="new-password"
              value={password}
              onFocus={() => password === MASK && setPassword("")}
              onBlur={() => password === "" && data.has_password && setPassword(MASK)}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="relay-prefix">{t("settings.server.relay.prefix")}</Label>
            <Input id="relay-prefix" value={prefix} onChange={(e) => setPrefix(e.target.value)} />
            <p className="text-xs text-muted-foreground">{t("settings.server.relay.prefixHint", { prefix })}</p>
          </div>
          <div className="flex flex-col justify-end gap-2 text-sm">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={tls} onChange={(e) => setTls(e.target.checked)} />
              {t("settings.server.relay.tls")}
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={tlsVerify} disabled={!tls} onChange={(e) => setTlsVerify(e.target.checked)} />
              {t("settings.server.relay.tlsVerify")}
            </label>
          </div>
        </div>
        <div className="space-y-2 rounded-md border p-3">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={commands} onChange={(e) => setCommands(e.target.checked)} />
            {t("settings.server.relay.commands")}
          </label>
          <p className="text-xs text-muted-foreground">{t("settings.server.relay.commandsHint", { prefix })}</p>
          {commands ? (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="relay-actor">{t("settings.server.relay.actor")}</Label>
              <select id="relay-actor" className={selectClass} value={actor} onChange={(e) => setActor(e.target.value)}>
                <option value="">{t("settings.server.relay.actorNone")}</option>
                {(actors.data ?? []).map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.username}
                  </option>
                ))}
              </select>
              {data.commands_enabled && !data.actor_ok ? (
                <p role="alert" className="text-xs text-destructive">
                  {data.actor_error}
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            disabled={!valid || save.isPending}
            onClick={() =>
              save.mutate(body(), {
                onSuccess: () => toast.success(t("settings.server.saved")),
                onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("settings.server.saveError")),
              })
            }
          >
            {t("settings.server.save")}
          </Button>
          <Button type="button" variant="outline" disabled={!host.trim() || test.isPending} onClick={() => test.mutate(body())}>
            {test.isPending ? t("settings.server.relay.testing") : t("settings.server.relay.test")}
          </Button>
          {test.data ? (
            <span role="status" className={test.data.ok ? "text-sm text-emerald-600 dark:text-emerald-400" : "text-sm text-destructive"}>
              {test.data.ok ? t("settings.server.relay.testOk") : t("settings.server.relay.testFailed", { error: test.data.detail })}
            </span>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

/** Settings -> Server: generic external MQTT broker relay (publish status, accept pause/resume/stop). */
export function MqttRelayCard() {
  const q = useRelay();
  if (q.isLoading || !q.data) return <Skeleton className="h-48 w-full rounded-xl" />;
  return <Form data={q.data} />;
}
