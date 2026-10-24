/** "Was this print good?" prompt for the user's own finished jobs awaiting an outcome. */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { useArchiveVerdict } from "@/api/archives";
import { useAuth } from "@/api/auth";
import { api } from "@/api/client";
import { useFeatures } from "@/api/features";
import type { ArchiveOut } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

const SNOOZE_KEY = "archive-confirm-snooze";

function readSnoozed(): number[] {
  try {
    const v = JSON.parse(sessionStorage.getItem(SNOOZE_KEY) ?? "[]");
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

export function PostPrintConfirmDialog() {
  const { t } = useTranslation();
  const { data: me } = useAuth();
  const features = useFeatures();
  const verdict = useArchiveVerdict();
  const [snoozed, setSnoozed] = useState<number[]>(readSnoozed);
  const q = useQuery({
    queryKey: ["archives", "pending-confirm"] as const,
    queryFn: () => api.get<ArchiveOut[]>("/archives?confirm_pending=1&mine=1&limit=20"),
    enabled: !!features.data?.printer_enabled && me != null,
    refetchInterval: 30_000,
  });
  const now = Date.now();
  const pending = (q.data ?? []).find(
    (a) =>
      a.confirm_requested &&
      !a.user_verdict &&
      me != null &&
      a.created_by_user_id === me.id &&
      !snoozed.includes(a.id) &&
      (!a.confirm_expires_at || Date.parse(a.confirm_expires_at) > now),
  );
  if (!pending) return null;

  const snooze = () => {
    const next = [...snoozed, pending.id];
    try {
      sessionStorage.setItem(SNOOZE_KEY, JSON.stringify(next));
    } catch {
      /* storage unavailable: snooze lasts for this mount only */
    }
    setSnoozed(next);
  };
  const send = (v: "good" | "bad") =>
    verdict.mutate(
      { id: pending.id, verdict: v, source: "dialog" },
      { onError: () => toast.error(t("archiveX.verdict.failed")) },
    );
  const name = pending.model_name ?? pending.subtask_name ?? `#${pending.id}`;

  return (
    <Dialog open onOpenChange={(o) => !o && snooze()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("archiveX.verdict.question")}</DialogTitle>
          <DialogDescription>{t("archiveX.verdict.dialogDesc", { name, printer: pending.printer_name })}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="ghost" onClick={snooze}>
            {t("archiveX.verdict.later")}
          </Button>
          <Button variant="destructive" disabled={verdict.isPending} onClick={() => send("bad")}>
            {t("archiveX.verdict.badBtn")}
          </Button>
          <Button disabled={verdict.isPending} onClick={() => send("good")}>
            {t("archiveX.verdict.goodBtn")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
