import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useUpdateTag } from "@/api/tags";
import type { TagOut } from "@/api/types";
import { TagForm } from "@/components/tags/TagForm";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useDiscardGuard } from "@/components/ui/use-discard-guard";

/** Edit dialog for one tag (name/color/icon/description) with the app-wide discard guard. */
export function EditTagDialog({ tag, onClose }: { tag: TagOut | null; onClose: () => void }) {
  const { t } = useTranslation();
  const updateTag = useUpdateTag();
  const [dirty, setDirty] = useState(false);
  const guard = useDiscardGuard({
    onOpenChange: (open) => {
      if (!open) onClose();
    },
    dirty,
    busy: updateTag.isPending,
  });

  return (
    <>
      <Dialog open={tag !== null} onOpenChange={guard.requestChange}>
        <DialogContent dismissable={false}>
          <DialogHeader>
            <DialogTitle>{t("settings.tags.editTitle")}</DialogTitle>
          </DialogHeader>
          {tag && (
            <TagForm
              key={tag.id}
              initial={tag}
              submitLabel={t("settings.tags.save")}
              pending={updateTag.isPending}
              onCancel={() => guard.requestChange(false)}
              onDirtyChange={setDirty}
              onSubmit={(values) =>
                updateTag.mutate(
                  { id: tag.id, ...values },
                  {
                    onSuccess: () => {
                      toast.success(t("settings.tags.saved"));
                      onClose();
                    },
                  },
                )
              }
            />
          )}
          {updateTag.isError && (
            <p role="alert" className="text-sm text-destructive">
              {updateTag.error instanceof ApiError ? updateTag.error.detail : t("settings.tags.saveError")}
            </p>
          )}
        </DialogContent>
      </Dialog>
      {guard.confirm}
    </>
  );
}
