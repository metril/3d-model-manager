/** "Link to model" for an archive the auto-matcher could not tie to a library model. */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { useLinkArchiveModel } from "@/api/archives";
import { ApiError } from "@/api/client";
import { useModelSearchQuery } from "@/api/library";
import { Button } from "@/components/ui/button";
import { DialogClose, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";
import { Input } from "@/components/ui/input";
import { useDebouncedValue } from "@/lib/format";

export function LinkModelDialog({
  archiveId,
  open,
  onOpenChange,
}: {
  archiveId: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const link = useLinkArchiveModel();
  const q = useDebouncedValue(query, 300);
  const search = useModelSearchQuery(q);
  const items = q.trim() ? (search.data?.items ?? []) : [];

  function pick(id: number, name: string) {
    link.mutate(
      { id: archiveId, modelId: id },
      {
        onSuccess: () => {
          toast.success(t("runDetail.linkModel.done", { name }));
          onOpenChange(false);
        },
        onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("runDetail.linkModel.failed")),
      },
    );
  }

  return (
    <FormDialog open={open} onOpenChange={onOpenChange} busy={link.isPending} className="sm:max-w-md">
      <DialogHeader>
        <DialogTitle>{t("runDetail.linkModel.title")}</DialogTitle>
        <DialogDescription>{t("runDetail.linkModel.hint")}</DialogDescription>
      </DialogHeader>
      <Input
        aria-label={t("runDetail.linkModel.search")}
        placeholder={t("runDetail.linkModel.search")}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        autoFocus
      />
      <ul className="max-h-56 overflow-y-auto text-sm" data-testid="link-model-results">
        {items.map((m) => (
          <li key={m.id}>
            <button
              type="button"
              disabled={link.isPending}
              className="w-full rounded px-2 py-1.5 text-left hover:bg-muted"
              onClick={() => pick(m.id, m.name)}
            >
              {m.name}
            </button>
          </li>
        ))}
        {q.trim() && !search.isFetching && items.length === 0 ? (
          <li className="px-2 py-1.5 text-muted-foreground">{t("runDetail.linkModel.none")}</li>
        ) : null}
      </ul>
      <DialogFooter>
        <DialogClose asChild>
          <Button type="button" variant="outline">
            {t("common.cancel")}
          </Button>
        </DialogClose>
      </DialogFooter>
    </FormDialog>
  );
}
