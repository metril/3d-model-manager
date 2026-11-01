/** Settings -> Storage -> archive purge (old runs' blobs), with preview + manual run. */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  useArchivePurgeSettings,
  usePurgePreview,
  usePurgeStats,
  useRunPurge,
  useUpdateArchivePurgeSettings,
} from "@/api/archives";
import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import type { ArchivePurgeSettings } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { SwitchRow } from "@/components/ui/switch-row";
import { formatDateTime, humanizeBytes } from "@/lib/format";

const errText = (e: unknown, fb: string) => (e instanceof ApiError ? e.detail : fb);

export function ArchivePurgeCard() {
  const q = useArchivePurgeSettings();
  if (q.isLoading) return <Skeleton className="h-56 w-full rounded-xl" />;
  if (!q.data) return null;
  return <Form initial={q.data} />;
}

function Form({ initial }: { initial: ArchivePurgeSettings }) {
  const { t } = useTranslation();
  const isAdmin = useAuth().can("admin");
  const save = useUpdateArchivePurgeSettings();
  const preview = usePurgePreview();
  const run = useRunPurge();
  const stats = usePurgeStats();
  const [draft, setDraft] = useState(initial);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const set = <K extends keyof ArchivePurgeSettings>(k: K, v: ArchivePurgeSettings[K]) =>
    setDraft((d) => ({ ...d, [k]: v }));
  const days = draft.older_than_days;
  const invalid = !Number.isInteger(days) || days < 1 || days > 3650;
  const s = stats.data;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("archiveX.purge.title")}</CardTitle>
        <CardDescription>{t("archiveX.purge.desc")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <SwitchRow id="purge-enabled" label={t("archiveX.purge.enabled")} checked={draft.enabled} onCheckedChange={(v) => set("enabled", v)} />
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="purge-days">{t("archiveX.purge.olderThan")}</Label>
          <Input
            id="purge-days"
            type="number"
            min={1}
            max={3650}
            value={days}
            onChange={(e) => set("older_than_days", Number(e.target.value))}
            className="w-32"
          />
        </div>
        <SwitchRow id="purge-failed" label={t("archiveX.purge.keepFailed")} checked={draft.keep_failed} onCheckedChange={(v) => set("keep_failed", v)} />
        <SwitchRow id="purge-verdict" label={t("archiveX.purge.keepVerdict")} checked={draft.keep_with_verdict} onCheckedChange={(v) => set("keep_with_verdict", v)} />
        <div className="flex flex-wrap items-center gap-2">
          <Button
            disabled={invalid || save.isPending}
            onClick={() =>
              save.mutate(draft, {
                onSuccess: () => toast.success(t("archiveX.saved")),
                onError: (e) => toast.error(errText(e, t("archiveX.saveFailed"))),
              })
            }
          >
            {t("archiveX.save")}
          </Button>
          {isAdmin ? (
            <Button
              variant="outline"
              disabled={preview.isPending}
              onClick={() =>
                preview.mutate(undefined, { onError: (e) => toast.error(errText(e, t("archiveX.purge.previewFailed"))) })
              }
            >
              {t("archiveX.purge.preview")}
            </Button>
          ) : null}
        </div>
        {preview.data ? (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span data-testid="purge-preview">
              {t("archiveX.purge.previewResult", { count: preview.data.count, size: humanizeBytes(preview.data.bytes) })}
            </span>
            {isAdmin && preview.data.count > 0 ? (
              <>
                <Button size="sm" variant="destructive" disabled={run.isPending} onClick={() => setConfirmOpen(true)}>
                  {t("archiveX.purge.run")}
                </Button>
                <ConfirmDialog
                  open={confirmOpen}
                  onOpenChange={setConfirmOpen}
                  title={t("archiveX.purge.confirmTitle", { count: preview.data.count })}
                  description={t("archiveX.purge.confirmDesc")}
                  confirmLabel={t("archiveX.purge.run")}
                  destructive
                  onConfirm={() =>
                    run.mutate(undefined, {
                      onSuccess: (r) => {
                        toast.success(t("archiveX.purge.done", { count: r.deleted, size: humanizeBytes(r.bytes) }));
                        preview.reset();
                        void stats.refetch();
                      },
                      onError: (e) => toast.error(errText(e, t("archiveX.purge.runFailed"))),
                    })
                  }
                />
              </>
            ) : null}
          </div>
        ) : null}
        {s ? (
          <p className="text-xs text-muted-foreground" data-testid="purge-stats">
            {s.last_run_at
              ? t("archiveX.purge.lastRun", {
                  when: formatDateTime(s.last_run_at),
                  count: s.last_deleted,
                  size: humanizeBytes(s.last_bytes),
                })
              : t("archiveX.purge.neverRun")}
            {s.next_run ? ` · ${t("archiveX.purge.nextRun", { when: s.next_run })}` : ""}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
