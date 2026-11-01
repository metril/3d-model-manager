import { useState } from "react";
import { ChevronRightIcon, FolderIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { isMoveTargetDisabled, validateName, type DragItems } from "@/api/fileOps";
import { dirName, findNode, type TreeNode } from "@/components/model-detail/explorer/tree";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { FileOut } from "@/api/types";

/** Name prompt shared by Rename and New folder. `siblings` excludes the entry being renamed. */
export function NameDialog({
  title,
  description,
  label,
  initial,
  siblings,
  confirmLabel,
  pendingLabel,
  pending,
  onSubmit,
  onClose,
}: {
  title: string;
  description?: string;
  label: string;
  initial: string;
  siblings: readonly string[];
  confirmLabel: string;
  pendingLabel: string;
  pending?: boolean;
  onSubmit: (name: string) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [value, setValue] = useState(initial);
  const trimmed = value.trim();
  const unchanged = trimmed === initial;
  const error = unchanged ? null : validateName(value, siblings);
  const canSubmit = !pending && !unchanged && trimmed !== "" && error === null;

  return (
    <Dialog open onOpenChange={(open) => !open && !pending && onClose()}>
      <DialogContent className="sm:max-w-md">
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (canSubmit) onSubmit(trimmed);
          }}
        >
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            {description ? <DialogDescription>{description}</DialogDescription> : null}
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="explorer-name">{label}</Label>
            <Input
              id="explorer-name"
              autoFocus
              value={value}
              onChange={(event) => setValue(event.target.value)}
              aria-invalid={error !== null}
              aria-describedby={error ? "explorer-name-error" : undefined}
              disabled={pending}
            />
            {error ? (
              <p id="explorer-name-error" role="alert" className="text-xs text-destructive">
                {t(`explorer.manage.nameError.${error}`)}
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={pending}>
              {t("explorer.manage.cancel")}
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              {pending ? pendingLabel : confirmLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Folder picker over the (client) tree: browse into folders, then "Move here". */
export function MoveDialog({
  items,
  allFiles,
  tree,
  startFolder,
  pending,
  onSubmit,
  onClose,
}: {
  items: DragItems;
  allFiles: readonly FileOut[];
  tree: TreeNode;
  startFolder: string;
  pending?: boolean;
  onSubmit: (dest: string) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [dest, setDest] = useState(findNode(tree, startFolder) ? startFolder : "");
  const node = findNode(tree, dest) ?? tree;
  const count = items.fileIds.length + items.folders.length;
  const dirs = node.folders.filter((folder) => !items.folders.includes(folder.path));
  const disabled = pending || isMoveTargetDisabled(items, dest, allFiles);

  return (
    <Dialog open onOpenChange={(open) => !open && !pending && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("explorer.manage.moveTitle", { count })}</DialogTitle>
          <DialogDescription>
            {t("explorer.manage.moveDestination", { path: dest === "" ? t("explorer.root") : dest })}
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-64 space-y-0.5 overflow-y-auto rounded-md border p-1">
          {dest !== "" ? (
            <button type="button" className="w-full rounded px-2 py-1.5 text-left text-sm hover:bg-muted" onClick={() => setDest(dirName(dest))}>
              {t("explorer.manage.up")}
            </button>
          ) : null}
          {dirs.length === 0 ? (
            <p className="px-2 py-1.5 text-sm text-muted-foreground">{t("explorer.manage.noSubfolders")}</p>
          ) : (
            dirs.map((folder) => (
              <button
                key={folder.path}
                type="button"
                className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-muted"
                onClick={() => setDest(folder.path)}
              >
                <FolderIcon className="size-4 text-muted-foreground" />
                <span className="flex-1 truncate">{folder.name}</span>
                <ChevronRightIcon className="size-3.5 text-muted-foreground" />
              </button>
            ))
          )}
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose} disabled={pending}>
            {t("explorer.manage.cancel")}
          </Button>
          <Button type="button" onClick={() => onSubmit(dest)} disabled={disabled}>
            {pending ? t("explorer.manage.moving") : t("explorer.manage.moveHere")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
