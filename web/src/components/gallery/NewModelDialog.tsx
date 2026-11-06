import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { useCreateModel } from "@/api/library";
import { api, ApiError } from "@/api/client";
import { CategoryPicker } from "@/components/gallery/CategoryPicker";
import { Slot } from "radix-ui";

import { Button } from "@/components/ui/button";
import { DialogClose, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useTranslation } from "react-i18next";

export function NewModelDialog({ trigger }: { trigger: React.ReactNode }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [categoryId, setCategoryId] = useState<number | null>(null);
  const navigate = useNavigate();
  const createModel = useCreateModel();
  const queryClient = useQueryClient();

  function reset() {
    setName("");
    setDescription("");
    setCategoryId(null);
    createModel.reset();
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    createModel.mutate(
      { name, description: description || undefined },
      {
        onSuccess: async (model) => {
          // `ModelCreate` has no `category_id` field (the backend only
          // accepts it via `PATCH` on an existing model) -- so an initial
          // category assignment is a follow-up patch, not part of the
          // create call itself. Best-effort: a failed patch here shouldn't
          // block navigating to the freshly-created model.
          if (categoryId !== null) {
            try {
              await api.patch(`/models/${model.slug}`, {
                category_id: categoryId,
              });
              void queryClient.invalidateQueries({ queryKey: ["models"] });
              void queryClient.invalidateQueries({ queryKey: ["storage"] });
            } catch {
              // The model itself was created fine; only the follow-up
              // category assignment failed. Surface that specifically
              // rather than swallowing it -- the user can still set the
              // category from the detail page, but shouldn't be left
              // thinking it was already applied.
              toast.warning(t("newModel.categoryFailed"));
            }
          }
          setOpen(false);
          reset();
          void navigate({ to: "/models/$slug", params: { slug: model.slug } });
        },
      },
    );
  }

  const errorMessage =
    createModel.error instanceof ApiError
      ? createModel.error.detail
      : createModel.error
        ? t("newModel.createFailed")
        : null;

  return (
    <>
      <Slot.Root onClick={() => setOpen(true)}>{trigger}</Slot.Root>
      <FormDialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) reset();
        }}
        dirty={name !== "" || description !== "" || categoryId !== null}
        busy={createModel.isPending}
      >
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>{t("newModel.title")}</DialogTitle>
            <DialogDescription>{t("newModel.desc")}</DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-3 py-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="new-model-name">{t("newModel.name")}</Label>
              <Input
                id="new-model-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                required
                autoFocus
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="new-model-description">{t("newModel.description")}</Label>
              <Textarea
                id="new-model-description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                rows={3}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>{t("newModel.category")}</Label>
              <CategoryPicker value={categoryId} onChange={setCategoryId} />
            </div>
            {errorMessage ? (
              <p role="alert" className="text-sm text-destructive">
                {errorMessage}
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="outline">
                {t("common.cancel")}
              </Button>
            </DialogClose>
            <Button type="submit" disabled={createModel.isPending || name.trim().length === 0}>
              {createModel.isPending ? t("newModel.creating") : t("newModel.create")}
            </Button>
          </DialogFooter>
        </form>
      </FormDialog>
    </>
  );
}
