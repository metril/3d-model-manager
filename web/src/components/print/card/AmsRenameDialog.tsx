/** T3: custom AMS unit label (empty clears it). */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { useAmsLabel } from "@/api/printerOps";
import { Button } from "@/components/ui/button";
import { DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function AmsRenameDialog({
  printerId,
  amsId,
  current,
  open,
  onOpenChange,
}: {
  printerId: number;
  amsId: number;
  current: string;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const { t } = useTranslation();
  const mut = useAmsLabel(printerId);
  const [label, setLabel] = useState(current);
  async function save() {
    try {
      await mut.mutateAsync({ amsId, label });
      toast.success(t("printerOps.ams.saved"));
      onOpenChange(false);
    } catch {
      /* toasted */
    }
  }
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} dirty={label !== current} busy={mut.isPending} className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{t("printerOps.ams.renameTitle")}</DialogTitle>
        </DialogHeader>
        <div className="space-y-1">
          <Label htmlFor="ams-label">{t("printerOps.ams.label")}</Label>
          <Input id="ams-label" value={label} maxLength={100} onChange={(e) => setLabel(e.target.value)} />
        </div>
        <DialogFooter showCloseButton closeLabel={t("common.cancel", { defaultValue: "Cancel" })}>
          <Button type="button" disabled={mut.isPending} onClick={() => void save()}>
            {t("printerOps.slot.save")}
          </Button>
        </DialogFooter>
    </FormDialog>
  );
}
