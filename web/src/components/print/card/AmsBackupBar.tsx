import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { useAmsBackup, useAmsBackupSave, useAmsRestore } from "@/api/printerOps";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { formatDateTime } from "@/lib/format";

/** Snapshot all AMS slot configs and re-apply them later (count-confirmed restore). */
export function AmsBackupBar({ printerId, busy }: { printerId: number; busy: boolean }) {
  const { t } = useTranslation();
  const backup = useAmsBackup(printerId);
  const save = useAmsBackupSave(printerId);
  const restore = useAmsRestore(printerId);
  const [confirm, setConfirm] = useState(false);
  const count = backup.data?.slots?.length ?? 0;

  async function doRestore() {
    try {
      const r = await restore.mutateAsync();
      if (r.failed > 0) toast.warning(t("printerOps.ams.restorePartial", { ok: r.restored + r.pending, failed: r.failed }));
      else toast.success(t("printerOps.ams.restoreDone", { count: r.restored + r.pending }));
    } catch {
      /* toasted by the hook */
    }
  }

  return (
    <div className="flex items-center gap-1" data-slot="ams-backup-bar">
      <Button
        type="button"
        variant="outline"
        size="xs"
        disabled={save.isPending}
        onClick={() =>
          save.mutate(undefined, { onSuccess: (d) => toast.success(t("printerOps.ams.backupSaved", { count: d.slots.length })) })
        }
      >
        {t("printerOps.ams.backupSave")}
      </Button>
      {count > 0 ? (
        <Button type="button" variant="outline" size="xs" disabled={busy || restore.isPending} onClick={() => setConfirm(true)}>
          {t("printerOps.ams.restore")} ({count})
        </Button>
      ) : null}
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={t("printerOps.ams.restoreTitle")}
        description={t("printerOps.ams.restoreDesc", { count, when: formatDateTime(backup.data?.taken_at) })}
        confirmLabel={t("printerOps.ams.restore")}
        destructive
        onConfirm={() => void doRestore()}
      />
    </div>
  );
}
