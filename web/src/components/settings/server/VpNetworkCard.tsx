import { useState } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import { useServerStatus, useUpdateServerSettings, type ServerSettingsOut } from "@/api/serverSettings";
import { DisabledReason } from "@/components/settings/server/DisabledReason";
import { Field } from "@/components/settings/server/Field";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

const validPort = (n: number) => Number.isInteger(n) && n >= 1 && n <= 65535;

export function VpNetworkCard({ data }: { data: ServerSettingsOut }) {
  const { t } = useTranslation();
  const save = useUpdateServerSettings();
  const status = useServerStatus();
  const c = data.config;
  const [bind, setBind] = useState(c.vp_bind);
  const [ssdp, setSsdp] = useState(String(c.vp_port_ssdp));
  const [mqtt, setMqtt] = useState(String(c.vp_port_mqtt));
  const [ftp, setFtp] = useState(String(c.vp_port_ftp));
  const [pmin, setPmin] = useState(String(c.vp_pasv_min));
  const [pmax, setPmax] = useState(String(c.vp_pasv_max));
  const [pasvAddr, setPasvAddr] = useState(c.vp_pasv_address ?? "");

  const n = { ssdp: Number(ssdp), mqtt: Number(mqtt), ftp: Number(ftp), min: Number(pmin), max: Number(pmax) };
  let error: string | null = null;
  if (bind.trim() === "") error = t("settings.server.vp.errBind");
  else if (![n.ssdp, n.mqtt, n.ftp, n.min, n.max].every(validPort)) error = t("settings.server.vp.errPort");
  else if (new Set([n.ssdp, n.mqtt, n.ftp]).size !== 3) error = t("settings.server.vp.errDistinct");
  else if (n.min > n.max) error = t("settings.server.vp.errRange");
  else if ([n.ssdp, n.mqtt, n.ftp].some((p) => p >= n.min && p <= n.max)) error = t("settings.server.vp.errOverlap");

  const next = {
    vp_bind: bind.trim(),
    vp_port_ssdp: n.ssdp,
    vp_port_mqtt: n.mqtt,
    vp_port_ftp: n.ftp,
    vp_pasv_min: n.min,
    vp_pasv_max: n.max,
    vp_pasv_address: pasvAddr.trim() || null,
  };
  const dirty = (Object.keys(next) as (keyof typeof next)[]).some((k) => next[k] !== c[k]);
  const vp = status.data?.virtual_printer;
  const state = vp?.state ?? "offline";
  const sv = data.seeded_from_env;
  const num = (id: string, label: string, field: string, v: string, set: (s: string) => void) => (
    <Field id={id} label={label} field={field} seeded={sv}>
      <Input id={id} type="number" min={1} max={65535} value={v} onChange={(e) => set(e.target.value)} />
    </Field>
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {t("settings.server.vp.title")}
          <Badge variant={state === "error" ? "destructive" : state === "running" ? "secondary" : "outline"}>
            {status.isLoading ? t("settings.server.vp.state.loading") : t(`settings.server.vp.state.${state}`)}
          </Badge>
        </CardTitle>
        <CardDescription>{t("settings.server.vp.desc")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {vp?.errors.length ? (
          <ul role="alert" className="list-disc pl-5 text-sm text-destructive">
            {vp.errors.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        ) : null}
        <div className="grid gap-3 sm:grid-cols-3">
          <Field id="srv-vp-bind" label={t("settings.server.vp.bind")} field="vp_bind" seeded={sv}>
            <Input id="srv-vp-bind" value={bind} onChange={(e) => setBind(e.target.value)} />
          </Field>
          {num("srv-vp-ssdp", t("settings.server.vp.ssdp"), "vp_port_ssdp", ssdp, setSsdp)}
          {num("srv-vp-mqtt", t("settings.server.vp.mqtt"), "vp_port_mqtt", mqtt, setMqtt)}
          {num("srv-vp-ftp", t("settings.server.vp.ftp"), "vp_port_ftp", ftp, setFtp)}
          {num("srv-vp-pmin", t("settings.server.vp.pasvMin"), "vp_pasv_min", pmin, setPmin)}
          {num("srv-vp-pmax", t("settings.server.vp.pasvMax"), "vp_pasv_max", pmax, setPmax)}
          <Field id="srv-vp-pasv-addr" label={t("settings.server.vp.pasvAddress")} field="vp_pasv_address" seeded={sv} className="sm:col-span-3">
            <Input id="srv-vp-pasv-addr" value={pasvAddr} placeholder={t("settings.server.vp.pasvAddressHint")} onChange={(e) => setPasvAddr(e.target.value)} />
          </Field>
        </div>
        {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
        <DisabledReason reason={!dirty ? t("settings.server.noChanges") : null}>
        <Button
          type="button"
          disabled={!!error || !dirty || save.isPending}
          onClick={() =>
            save.mutate(next, {
              onSuccess: () => toast.success(t("settings.server.saved")),
              onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("settings.server.saveError")),
            })
          }
        >
          {t("settings.server.save")}
        </Button>
        </DisabledReason>
      </CardContent>
    </Card>
  );
}
