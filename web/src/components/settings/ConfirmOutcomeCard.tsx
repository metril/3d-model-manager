/** Settings -> Printer -> post-print outcome confirmation ("was it good?"). */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { useConfirmSettings, useUpdateConfirmSettings } from "@/api/archives";
import { ApiError } from "@/api/client";
import type { ConfirmSettings } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";

const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";

export function ConfirmOutcomeCard() {
  const q = useConfirmSettings();
  if (q.isLoading) return <Skeleton className="h-48 w-full rounded-xl" />;
  if (!q.data) return null;
  return <Form initial={q.data} />;
}

function Form({ initial }: { initial: ConfirmSettings }) {
  const { t } = useTranslation();
  const save = useUpdateConfirmSettings();
  const [d, setD] = useState(initial);
  const set = <K extends keyof ConfirmSettings>(k: K, v: ConfirmSettings[K]) => setD((x) => ({ ...x, [k]: v }));
  const invalid = !Number.isInteger(d.default_after_h) || d.default_after_h < 1 || d.default_after_h > 720;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("archiveX.confirm.title")}</CardTitle>
        <CardDescription>{t("archiveX.confirm.desc")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-2">
          <Switch id="cf-enabled" checked={d.enabled} onCheckedChange={(v) => set("enabled", v)} />
          <Label htmlFor="cf-enabled">{t("archiveX.confirm.enabled")}</Label>
        </div>
        <div className="flex items-center gap-2">
          <Switch id="cf-ext" checked={d.external_prints} onCheckedChange={(v) => set("external_prints", v)} />
          <Label htmlFor="cf-ext">{t("archiveX.confirm.external")}</Label>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="cf-outcome">{t("archiveX.confirm.defaultOutcome")}</Label>
          <select
            id="cf-outcome"
            className={selectClass}
            value={d.default_outcome ?? ""}
            onChange={(e) => set("default_outcome", (e.target.value || null) as ConfirmSettings["default_outcome"])}
          >
            <option value="">{t("archiveX.confirm.outcomeNone")}</option>
            <option value="good">{t("archiveX.verdict.goodBtn")}</option>
            <option value="bad">{t("archiveX.verdict.badBtn")}</option>
          </select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="cf-after">{t("archiveX.confirm.afterH")}</Label>
          <Input
            id="cf-after"
            type="number"
            min={1}
            max={720}
            className="w-32"
            value={d.default_after_h}
            onChange={(e) => set("default_after_h", Number(e.target.value))}
          />
        </div>
        <div className="flex items-center gap-2">
          <Switch id="cf-plate" checked={d.default_good_on_plate_clear} onCheckedChange={(v) => set("default_good_on_plate_clear", v)} />
          <Label htmlFor="cf-plate">{t("archiveX.confirm.goodOnPlateClear")}</Label>
        </div>
        <Button
          disabled={invalid || save.isPending}
          onClick={() =>
            save.mutate(d, {
              onSuccess: () => toast.success(t("archiveX.saved")),
              onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("archiveX.saveFailed")),
            })
          }
        >
          {t("archiveX.save")}
        </Button>
      </CardContent>
    </Card>
  );
}
