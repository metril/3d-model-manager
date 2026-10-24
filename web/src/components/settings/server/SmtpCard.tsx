import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useSmtpTest, useUpdateServerSettings, type ServerSettingsOut, type SmtpSecurity } from "@/api/serverSettings";
import { DisabledReason } from "@/components/settings/server/DisabledReason";
import { Field } from "@/components/settings/server/Field";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";
const MASK = "***";
const SECURITY: SmtpSecurity[] = ["none", "starttls", "tls"];
const DEFAULT_PORT: Record<SmtpSecurity, number> = { none: 25, starttls: 587, tls: 465 };

/** Outgoing mail (2FA codes, notification emails). Password is write-only: "***" = keep. */
export function SmtpCard({ data }: { data: ServerSettingsOut }) {
  const { t } = useTranslation();
  const save = useUpdateServerSettings();
  const test = useSmtpTest();
  const c = data.config.smtp;
  const hasPassword = c.password === MASK;
  const [host, setHost] = useState(c.host);
  const [port, setPort] = useState(String(c.port));
  const [security, setSecurity] = useState<SmtpSecurity>(c.security);
  const [username, setUsername] = useState(c.username);
  // Untouched field stays "***" (keep); typing replaces; emptying clears.
  const [password, setPassword] = useState(hasPassword ? MASK : (c.password ?? ""));
  const [fromAddr, setFromAddr] = useState(c.from_addr);
  const [fromName, setFromName] = useState(c.from_name);
  const [to, setTo] = useState("");
  const [cleared, setCleared] = useState(false);

  const portN = Number(port);
  const valid = Number.isInteger(portN) && portN >= 1 && portN <= 65535;
  const dirty =
    host !== c.host ||
    portN !== c.port ||
    security !== c.security ||
    username !== c.username ||
    password !== (c.password ?? "") ||
    fromAddr !== c.from_addr ||
    fromName !== c.from_name;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("smtp.title")}</CardTitle>
        <CardDescription>{t("smtp.desc")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field id="smtp-host" label={t("smtp.host")} field="smtp_host" seeded={data.seeded_from_env}>
            <Input id="smtp-host" value={host} onChange={(e) => setHost(e.target.value)} placeholder="smtp.example.com" />
          </Field>
          <Field id="smtp-port" label={t("smtp.port")} field="smtp_port" seeded={data.seeded_from_env}>
            <Input id="smtp-port" type="number" min={1} max={65535} value={port} onChange={(e) => setPort(e.target.value)} />
          </Field>
          <Field id="smtp-security" label={t("smtp.security")} field="smtp_security" seeded={data.seeded_from_env}>
            <select
              id="smtp-security"
              className={selectClass}
              value={security}
              onChange={(e) => {
                const next = e.target.value as SmtpSecurity;
                // Follow the conventional port while it's still the previous default.
                if (portN === DEFAULT_PORT[security]) setPort(String(DEFAULT_PORT[next]));
                setSecurity(next);
              }}
            >
              {SECURITY.map((s) => (
                <option key={s} value={s}>
                  {t(`smtp.securityOpt.${s}`)}
                </option>
              ))}
            </select>
          </Field>
          <Field id="smtp-username" label={t("smtp.username")} field="smtp_username" seeded={data.seeded_from_env}>
            <Input id="smtp-username" autoComplete="off" value={username} onChange={(e) => setUsername(e.target.value)} />
          </Field>
          <Field id="smtp-password" label={t("smtp.password")} field="smtp_password" seeded={data.seeded_from_env}>
            <Input
              id="smtp-password"
              type="password"
              autoComplete="new-password"
              value={password}
              onFocus={() => password === MASK && setPassword("")}
              onBlur={() => password === "" && hasPassword && !cleared && setPassword(MASK)}
              onChange={(e) => {
                setPassword(e.target.value);
                if (e.target.value) setCleared(false);
              }}
            />
            <p className="text-xs text-muted-foreground">
              {cleared ? t("smtp.passwordClearing") : hasPassword ? t("smtp.passwordKeep") : t("smtp.passwordUnset")}
            </p>
            {hasPassword ? (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="self-start"
                onClick={() => {
                  setCleared(!cleared);
                  setPassword(cleared ? MASK : "");
                }}
              >
                {cleared ? t("smtp.passwordKeepBtn") : t("smtp.passwordClear")}
              </Button>
            ) : null}
          </Field>
          <Field id="smtp-from-addr" label={t("smtp.fromAddr")} field="smtp_from" seeded={data.seeded_from_env}>
            <Input id="smtp-from-addr" type="email" value={fromAddr} onChange={(e) => setFromAddr(e.target.value)} />
          </Field>
          <Field id="smtp-from-name" label={t("smtp.fromName")} seeded={data.seeded_from_env}>
            <Input id="smtp-from-name" value={fromName} onChange={(e) => setFromName(e.target.value)} />
          </Field>
        </div>
        {!valid ? (
          <p role="alert" className="text-sm text-destructive">
            {t("smtp.invalidPort")}
          </p>
        ) : null}
        <DisabledReason reason={!dirty ? t("settings.server.noChanges") : null}>
          <Button
            type="button"
            disabled={!valid || !dirty || save.isPending}
            onClick={() =>
              save.mutate(
                { smtp: { host, port: portN, security, username, password, from_addr: fromAddr, from_name: fromName } },
                {
                  onSuccess: () => toast.success(t("settings.server.saved")),
                  onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("settings.server.saveError")),
                },
              )
            }
          >
            {t("settings.server.save")}
          </Button>
        </DisabledReason>
        <div className="space-y-2 border-t pt-4">
          <Field id="smtp-test-to" label={t("smtp.testTo")} seeded={data.seeded_from_env}>
            <div className="flex gap-2">
              <Input id="smtp-test-to" type="email" value={to} onChange={(e) => setTo(e.target.value)} />
              <Button type="button" variant="outline" disabled={!to.trim() || test.isPending} onClick={() => test.mutate(to.trim())}>
                {test.isPending ? t("smtp.testing") : t("smtp.test")}
              </Button>
            </div>
          </Field>
          <p className="text-xs text-muted-foreground">{t("smtp.testHint")}</p>
          {test.data?.ok ? (
            <p role="status" className="text-sm text-emerald-600 dark:text-emerald-400">
              {t("smtp.testOk")}
            </p>
          ) : test.data ? (
            <p role="alert" className="text-sm text-destructive">
              {t("smtp.testFailed", { error: test.data.error ?? "" })}
            </p>
          ) : test.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {test.error instanceof ApiError ? test.error.detail : t("smtp.testFailed", { error: "" })}
            </p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
