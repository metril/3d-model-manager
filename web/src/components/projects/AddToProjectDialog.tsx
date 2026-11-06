import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { useAddModelsToProject, useCreateProject, useProjects } from "@/api/projects";
import { Button } from "@/components/ui/button";
import { DialogClose, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";
import { Input } from "@/components/ui/input";

/** Pick an existing project (or create one) and add the given models to its BOM. */
export function AddToProjectDialog({
  open,
  onOpenChange,
  modelIds,
  onDone,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  modelIds: number[];
  onDone?: () => void;
}) {
  const { t } = useTranslation();
  const projects = useProjects();
  const add = useAddModelsToProject();
  const create = useCreateProject();
  const [choice, setChoice] = useState("");
  const [newName, setNewName] = useState("");
  // A project made by a first attempt whose "add" step then failed: a retry
  // must reuse it instead of creating a duplicate.
  const [createdId, setCreatedId] = useState<number | null>(null);
  const list = (projects.data ?? []).filter((p) => p.status !== "archived");
  const creating = choice === "new";
  const busy = add.isPending || create.isPending;
  const canSubmit = !busy && (creating ? newName.trim() !== "" : choice !== "");

  async function submit() {
    try {
      let projectId = Number(choice);
      if (creating) {
        if (createdId == null) {
          projectId = (await create.mutateAsync({ name: newName.trim() })).id;
          setCreatedId(projectId);
        } else projectId = createdId;
      }
      await add.mutateAsync({ projectId, modelIds });
      toast.success(t("projects.added", { count: modelIds.length }));
      handleOpenChange(false);
      onDone?.();
    } catch {
      // global mutation error toast
    }
  }

  function handleOpenChange(next: boolean) {
    onOpenChange(next);
    if (!next) {
      setChoice("");
      setNewName("");
      setCreatedId(null);
    }
  }

  return (
    <FormDialog
      open={open}
      onOpenChange={handleOpenChange}
      dirty={choice !== "" || newName !== ""}
      busy={busy}
    >
      <DialogHeader>
        <DialogTitle>{t("projects.addTitle")}</DialogTitle>
        <DialogDescription>
          {t("projects.addDesc", { count: modelIds.length })}
        </DialogDescription>
      </DialogHeader>
      <select
        aria-label={t("projects.project")}
        className="h-9 w-full rounded-md border bg-background px-2 text-sm"
        value={choice}
        onChange={(e) => setChoice(e.target.value)}
      >
        <option value="">{t("projects.choose")}</option>
        {list.map((p) => (
          <option key={p.id} value={String(p.id)}>
            {p.name}
          </option>
        ))}
        <option value="new">{t("projects.newOption")}</option>
      </select>
      {creating && (
        <Input
          autoFocus
          aria-label={t("projects.newName")}
          placeholder={t("projects.namePlaceholder")}
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
      )}
      <DialogFooter>
        <DialogClose asChild>
          <Button type="button" variant="outline">
            {t("common.cancel")}
          </Button>
        </DialogClose>
        <Button type="button" disabled={!canSubmit} onClick={() => void submit()}>
          {t("common.add")}
        </Button>
      </DialogFooter>
    </FormDialog>
  );
}
