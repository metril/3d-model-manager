import { useState } from "react";
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
  const projects = useProjects();
  const add = useAddModelsToProject();
  const create = useCreateProject();
  const [choice, setChoice] = useState("");
  const [newName, setNewName] = useState("");
  const list = (projects.data ?? []).filter((p) => p.status !== "archived");
  const creating = choice === "new";
  const busy = add.isPending || create.isPending;
  const canSubmit = !busy && (creating ? newName.trim() !== "" : choice !== "");

  async function submit() {
    try {
      let projectId = Number(choice);
      if (creating) projectId = (await create.mutateAsync({ name: newName.trim() })).id;
      await add.mutateAsync({ projectId, modelIds });
      toast.success(`Added ${modelIds.length} model${modelIds.length === 1 ? "" : "s"} to project`);
      onOpenChange(false);
      onDone?.();
    } catch {
      // global mutation error toast
    }
  }

  return (
    <FormDialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) {
          setChoice("");
          setNewName("");
        }
      }}
      dirty={choice !== "" || newName !== ""}
      busy={busy}
    >
      <DialogHeader>
        <DialogTitle>Add to project</DialogTitle>
        <DialogDescription>
          Adds {modelIds.length} model{modelIds.length === 1 ? "" : "s"} to a project&apos;s bill of materials.
        </DialogDescription>
      </DialogHeader>
      <select
        aria-label="Project"
        className="h-9 w-full rounded-md border bg-background px-2 text-sm"
        value={choice}
        onChange={(e) => setChoice(e.target.value)}
      >
        <option value="">Choose a project…</option>
        {list.map((p) => (
          <option key={p.id} value={String(p.id)}>
            {p.name}
          </option>
        ))}
        <option value="new">+ New project…</option>
      </select>
      {creating && (
        <Input
          autoFocus
          aria-label="New project name"
          placeholder="Project name"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
      )}
      <DialogFooter>
        <DialogClose asChild>
          <Button type="button" variant="outline">
            Cancel
          </Button>
        </DialogClose>
        <Button type="button" disabled={!canSubmit} onClick={() => void submit()}>
          Add
        </Button>
      </DialogFooter>
    </FormDialog>
  );
}
