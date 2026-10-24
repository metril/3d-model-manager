import { useState } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import { useUpdateServerSettings, type ServerSettingsOut } from "@/api/serverSettings";
import { Field } from "@/components/settings/server/Field";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

const norm = (v: string) => v.trim().replace(/\/+$/, "");

export function PublicUrlCard({ data }: { data: ServerSettingsOut }) {
  const { t } = useTranslation();
  const save = useUpdateServerSettings();
  const [value, setValue] = useState(data.config.public_url ?? "");
  const [confirming, setConfirming] = useState(false);
  const origin = window.location.origin;
  const url = norm(value);
  const validUrl = url === "" || /^https?:\/\/[^\s/]+/i.test(url);
  const mismatch = url !== "" && validUrl && !url.toLowerCase().startsWith(origin.toLowerCase());
  const dirty = url !== (data.config.public_url ?? "");
  const secure = url === "" ? null : url.toLowerCase().startsWith("https://");

  const commit = () => {
    setConfirming(false);
    save.mutate(
      { ...data.config, public_url: url || null },
      {
        onSuccess: () => toast.success(t("settings.server.saved")),
        onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("settings.server.saveError")),
      },
    );
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settings.server.publicUrl.title")}</CardTitle>
        <CardDescription>{t("settings.server.publicUrl.desc")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Field id="srv-public-url" label={t("settings.server.publicUrl.label")} field="public_url" seeded={data.seeded_from_env}>
          <div className="flex gap-2">
            <Input
              id="srv-public-url"
              value={value}
              placeholder="https://models.example.com"
              onChange={(e) => setValue(e.target.value)}
            />
            <Button
              type="button"
              variant="outline"
              disabled={norm(value) === origin}
              title={norm(value) === origin ? t("settings.server.publicUrl.alreadyOrigin") : undefined}
              onClick={() => setValue(origin)}
            >
              {t("settings.server.publicUrl.useOrigin")}
            </Button>
          </div>
        </Field>
        {!validUrl ? (
          <p role="alert" className="text-sm text-destructive">{t("settings.server.publicUrl.invalid")}</p>
        ) : null}
        {mismatch ? (
          <p role="alert" className="text-sm text-amber-600">
            {t("settings.server.publicUrl.mismatch", { origin })}
          </p>
        ) : null}
        <p className="text-xs text-muted-foreground">
          {secure === null
            ? t("settings.server.publicUrl.cookieRequest")
            : secure
              ? t("settings.server.publicUrl.cookieSecure")
              : t("settings.server.publicUrl.cookieInsecure")}
        </p>
        <Button
          type="button"
          disabled={!dirty || !validUrl || save.isPending}
          title={!dirty ? t("settings.server.noChanges") : undefined}
          onClick={() => (mismatch ? setConfirming(true) : commit())}
        >
          {t("settings.server.save")}
        </Button>
      </CardContent>
      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t("settings.server.publicUrl.confirmTitle")}</DialogTitle>
            <DialogDescription>{t("settings.server.publicUrl.confirmBody", { url, origin })}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirming(false)}>
              {t("settings.server.cancel")}
            </Button>
            <Button type="button" onClick={commit}>{t("settings.server.publicUrl.confirm")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
