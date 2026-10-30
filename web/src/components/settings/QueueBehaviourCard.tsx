/** Settings -> Printer "Queue behaviour" card (`GET/PUT /settings/queue`). */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import { useQueueSettings, useUpdateQueueSettings } from "@/api/queue";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";

export function QueueBehaviourCard() {
  const { t } = useTranslation();
  const settings = useQueueSettings();
  const update = useUpdateQueueSettings();
  const [plateClear, setPlateClear] = useState(false);
  const [headsUp, setHeadsUp] = useState("5");
  const [seeded, setSeeded] = useState(false);
  const [error, setError] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (settings.data && !seeded) {
      setPlateClear(settings.data.require_plate_clear);
      setHeadsUp(String(settings.data.pause_heads_up_min));
      setSeeded(true);
    }
  }, [settings.data, seeded]);

  if (settings.isLoading || (!seeded && !settings.isError)) return <Skeleton className="h-40 w-full rounded-xl" />;
  if (settings.isError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t("queueSched.behaviour_card.title")}</CardTitle>
          <CardDescription>
            {settings.error instanceof ApiError ? settings.error.detail : t("queueSched.behaviour_card.loadError")}
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  function save() {
    const n = Number(headsUp);
    const ok = headsUp.trim() !== "" && Number.isInteger(n) && n >= 1 && n <= 120;
    setError(!ok);
    if (!ok) return;
    update.mutate({ require_plate_clear: plateClear, pause_heads_up_min: n }, { onSuccess: () => setSaved(true) });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("queueSched.behaviour_card.title")}</CardTitle>
        <CardDescription>{t("queueSched.behaviour_card.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <label className="flex items-center gap-2 text-sm">
          <Switch
            checked={plateClear}
            onCheckedChange={(v) => {
              setSaved(false);
              setPlateClear(v);
            }}
            aria-label={t("queueSched.behaviour_card.plateClear")}
          />
          {t("queueSched.behaviour_card.plateClear")}
        </label>
        <div className="space-y-1.5">
          <Label htmlFor="qb-heads-up">{t("queueSched.behaviour_card.headsUp")}</Label>
          <Input
            id="qb-heads-up"
            type="number"
            className="w-28"
            value={headsUp}
            onChange={(e) => {
              setSaved(false);
              setHeadsUp(e.target.value);
            }}
          />
          <p className="text-xs text-muted-foreground">{t("queueSched.behaviour_card.headsUpHint")}</p>
        </div>
        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {t("queueSched.behaviour_card.errHeadsUp")}
          </p>
        ) : null}
        {update.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {update.error instanceof ApiError ? update.error.detail : t("queueSched.behaviour_card.saveError")}
          </p>
        ) : null}
        <div className="flex items-center gap-3">
          <Button type="button" onClick={save} disabled={update.isPending}>
            {t("queueSched.behaviour_card.save")}
          </Button>
          {saved ? <span className="text-sm text-muted-foreground">{t("queueSched.behaviour_card.saved")}</span> : null}
        </div>
      </CardContent>
    </Card>
  );
}
