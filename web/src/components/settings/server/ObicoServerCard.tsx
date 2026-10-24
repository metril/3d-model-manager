import { useState } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import { useObicoSettings, useSaveObicoSettings, useTestObico, type ObicoSettings } from "@/api/detection";
import { useServerStatus } from "@/api/serverSettings";
import { Field } from "@/components/settings/server/Field";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

export function ObicoServerCard() {
  const q = useObicoSettings();
  if (q.isLoading) return <Skeleton className="h-40 w-full rounded-xl" />;
  if (!q.data) return null;
  return <Form initial={q.data} />;
}

function Form({ initial }: { initial: ObicoSettings }) {
  const { t } = useTranslation();
  const save = useSaveObicoSettings();
  const test = useTestObico();
  const status = useServerStatus();
  const [url, setUrl] = useState(initial.url);
  const [cb, setCb] = useState(initial.callback_url);
  const dirty = url.trim() !== initial.url || cb.trim() !== initial.callback_url;
  const ok = status.data?.obico.ok;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {t("settings.server.obico.title")}
          {ok === undefined ? null : ok ? (
            <Badge variant="secondary">{t("settings.server.obico.healthy")}</Badge>
          ) : (
            <Badge variant="destructive">{t("settings.server.obico.unreachable")}</Badge>
          )}
        </CardTitle>
        <CardDescription>{t("settings.server.obico.desc")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field id="srv-obico-url" label={t("settings.server.obico.url")} seeded={{}}>
            <div className="flex gap-2">
              <Input id="srv-obico-url" value={url} placeholder="http://obico-ml:3333" onChange={(e) => setUrl(e.target.value)} />
              <Button
                type="button"
                variant="outline"
                disabled={test.isPending || url.trim() === ""}
                title={url.trim() === "" ? t("settings.server.obico.needUrl") : undefined}
                onClick={() => test.mutate(url.trim())}
              >
                {t("settings.server.obico.test")}
              </Button>
            </div>
            {test.data ? (
              <p role="status" className={test.data.ok ? "text-xs text-emerald-600" : "text-xs text-destructive"}>
                {test.data.ok ? t("settings.server.obico.connected") : t("settings.server.obico.failed", { detail: test.data.detail })}
              </p>
            ) : null}
          </Field>
          <Field id="srv-obico-cb" label={t("settings.server.obico.callback")} seeded={{}}>
            <Input id="srv-obico-cb" value={cb} placeholder="http://api:8000" onChange={(e) => setCb(e.target.value)} />
          </Field>
        </div>
        <Button
          type="button"
          disabled={!dirty || save.isPending}
          title={!dirty ? t("settings.server.noChanges") : undefined}
          onClick={() =>
            save.mutate(
              { ...initial, url: url.trim(), callback_url: cb.trim() },
              {
                onSuccess: () => toast.success(t("settings.server.saved")),
                onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("settings.server.saveError")),
              },
            )
          }
        >
          {t("settings.server.save")}
        </Button>
      </CardContent>
    </Card>
  );
}
