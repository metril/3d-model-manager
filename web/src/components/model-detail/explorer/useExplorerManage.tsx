/** Rename / move / new folder / bulk delete / drag-and-drop for the explorer (all gated on `canUpload`). */
import { useCallback, useMemo, useState, type DragEvent, type HTMLAttributes, type ReactNode } from "react";
import { FolderInputIcon, FolderPlusIcon, MoreHorizontalIcon, PencilIcon, Trash2Icon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  planMoveInto,
  planRenameFile,
  planRenameFolder,
  useDeleteFiles,
  useMoveFiles,
  type DragItems,
  type MovePlan,
} from "@/api/fileOps";
import { ApiError } from "@/api/client";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import {
  DND_TYPE,
  decodeDrag,
  dragItemsFor,
  encodeDrag,
  isExplorerDrag,
  itemsFromKeys,
} from "@/components/model-detail/explorer/dnd";
import { MoveDialog, NameDialog } from "@/components/model-detail/explorer/manageDialogs";
import type { ExplorerSelection } from "@/components/model-detail/explorer/selection";
import { baseName, dirName, findNode, flattenFiles, type TreeNode } from "@/components/model-detail/explorer/tree";
import { childNames, joinPath, rewriteFolders } from "@/components/model-detail/explorer/treeEdit";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import type { FileOut, ModelDetail } from "@/api/types";

type Dialog =
  | { kind: "renameFile"; file: FileOut }
  | { kind: "renameFolder"; path: string }
  | { kind: "move"; items: DragItems }
  | { kind: "newFolder" }
  | { kind: "delete"; ids: number[]; name?: string };

const errMsg = (e: unknown, fallback: string) =>
  e instanceof ApiError ? e.detail : e instanceof Error && e.message ? e.message : fallback;

function ManageMenu({
  label,
  onRename,
  onMove,
  onDelete,
}: {
  label: string;
  onRename: () => void;
  onMove: () => void;
  onDelete?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button type="button" variant="ghost" size="icon-sm" aria-label={t("explorer.manage.manage", { name: label })}>
          <MoreHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={onRename}>
          <PencilIcon className="size-4" />
          {t("explorer.manage.rename")}
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={onMove}>
          <FolderInputIcon className="size-4" />
          {t("explorer.manage.move")}
        </DropdownMenuItem>
        {onDelete ? (
          <DropdownMenuItem variant="destructive" onSelect={onDelete}>
            <Trash2Icon className="size-4" />
            {t("explorer.manage.delete")}
          </DropdownMenuItem>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export interface ExplorerManage {
  fileActions: (file: FileOut) => ReactNode;
  folderActions: (path: string) => ReactNode;
  newFolderButton: ReactNode;
  dialogs: ReactNode;
  busy: boolean;
  /** Bulk-bar actions over the current selection. */
  selectionMove: () => void;
  selectionDelete: () => void;
  onDragStartFile: (event: DragEvent, file: FileOut) => void;
  onDragStartFolder: (event: DragEvent, path: string) => void;
  dropProps: (path: string) => HTMLAttributes<HTMLElement>;
}

export function useExplorerManage({
  model,
  canUpload,
  allFiles,
  tree,
  extraFolders,
  setExtraFolders,
  currentFolder,
  setCurrentFolder,
  selection,
}: {
  model: ModelDetail;
  canUpload: boolean;
  allFiles: readonly FileOut[];
  /** Full tree including client-only folders. */
  tree: TreeNode;
  extraFolders: readonly string[];
  setExtraFolders: (update: (prev: string[]) => string[]) => void;
  currentFolder: string;
  setCurrentFolder: (path: string) => void;
  selection: ExplorerSelection;
}): ExplorerManage {
  const { t } = useTranslation();
  const move = useMoveFiles(model);
  const del = useDeleteFiles(model.slug);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const busy = move.isPending || del.isPending;
  const close = useCallback(() => setDialog(null), []);

  const allPaths = useMemo(() => [...allFiles.map((f) => f.rel_path), ...extraFolders], [allFiles, extraFolders]);
  const selected = useMemo(() => itemsFromKeys(selection.keys), [selection.keys]);

  const filesUnder = useCallback(
    (folder: string) => {
      const node = findNode(tree, folder);
      return node ? flattenFiles(node) : [];
    },
    [tree],
  );

  const runMove = useCallback(
    (plan: MovePlan, after?: () => void) => {
      if (plan.error === "into-self") {
        toast.error(t("explorer.manage.planError.into-self"));
        return;
      }
      if (plan.error === "clash") {
        toast.error(t("explorer.manage.planError.clash", { path: plan.clashPath }));
        return;
      }
      const rewrite = () =>
        setExtraFolders((prev) => plan.folderMoves.reduce((acc, m) => rewriteFolders(acc, m.from, m.to), prev));
      if (plan.moves.length === 0) {
        rewrite();
        after?.();
        return;
      }
      move.mutate(plan.moves, {
        onSuccess: () => {
          rewrite();
          selection.clear();
          toast.success(t("explorer.manage.moved", { count: plan.moves.length }));
          after?.();
        },
        onError: (e) => toast.error(errMsg(e, t("explorer.manage.moveFailed"))),
      });
    },
    [move, selection, setExtraFolders, t],
  );

  const dropOn = useCallback(
    (dest: string, items: DragItems) => {
      if (move.isPending) return;
      runMove(planMoveInto(allFiles, items, dest));
    },
    [allFiles, move.isPending, runMove],
  );

  const dropProps = useCallback(
    (path: string): HTMLAttributes<HTMLElement> => {
      if (!canUpload) return {};
      return {
        onDragOver: (event) => {
          if (!isExplorerDrag(event.dataTransfer)) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = "move";
          setOver(path);
        },
        onDragLeave: (event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOver((cur) => (cur === path ? null : cur));
        },
        onDrop: (event) => {
          if (!isExplorerDrag(event.dataTransfer)) return;
          event.preventDefault();
          event.stopPropagation();
          setOver(null);
          const items = decodeDrag(event.dataTransfer.getData(DND_TYPE));
          if (items) dropOn(path, items);
        },
        ...({ "data-drop-over": over === path } as object),
      };
    },
    [canUpload, dropOn, over],
  );

  const startDrag = useCallback(
    (event: DragEvent, item: Parameters<typeof dragItemsFor>[0]) => {
      const items = dragItemsFor(item, selected, selection.has);
      event.dataTransfer.setData(DND_TYPE, encodeDrag(items));
      event.dataTransfer.effectAllowed = "move";
    },
    [selected, selection.has],
  );
  const onDragStartFile = useCallback((event: DragEvent, file: FileOut) => startDrag(event, { kind: "file", id: file.id }), [startDrag]);
  const onDragStartFolder = useCallback((event: DragEvent, path: string) => startDrag(event, { kind: "folder", path }), [startDrag]);

  const requestDelete = useCallback(
    (items: DragItems, name?: string) => {
      const ids = new Set(items.fileIds);
      for (const folder of items.folders) for (const file of filesUnder(folder)) ids.add(file.id);
      if (ids.size === 0) {
        // Only client-only empty folders: nothing server-side to delete.
        setExtraFolders((prev) => prev.filter((p) => !items.folders.some((f) => p === f || p.startsWith(`${f}/`))));
        selection.clear();
        return;
      }
      setDialog({ kind: "delete", ids: [...ids], name });
    },
    [filesUnder, selection, setExtraFolders],
  );

  const fileActions = useCallback(
    (file: FileOut): ReactNode =>
      canUpload ? (
        <ManageMenu
          label={file.rel_path}
          onRename={() => setDialog({ kind: "renameFile", file })}
          onMove={() => setDialog({ kind: "move", items: { fileIds: [file.id], folders: [] } })}
        />
      ) : null,
    [canUpload],
  );

  const folderActions = useCallback(
    (path: string): ReactNode =>
      canUpload && path !== "" ? (
        <ManageMenu
          label={path}
          onRename={() => setDialog({ kind: "renameFolder", path })}
          onMove={() => setDialog({ kind: "move", items: { fileIds: [], folders: [path] } })}
          onDelete={() => requestDelete({ fileIds: [], folders: [path] }, baseName(path))}
        />
      ) : null,
    [canUpload, requestDelete],
  );

  const newFolderButton = canUpload ? (
    <Button type="button" variant="outline" size="sm" onClick={() => setDialog({ kind: "newFolder" })}>
      <FolderPlusIcon className="size-4" />
      {t("explorer.manage.newFolder")}
    </Button>
  ) : null;

  let dialogs: ReactNode = null;
  if (canUpload && dialog) {
    switch (dialog.kind) {
      case "renameFile": {
        const { file } = dialog;
        const parent = dirName(file.rel_path);
        const name = baseName(file.rel_path);
        dialogs = (
          <NameDialog
            title={t("explorer.manage.renameFile", { name })}
            label={t("explorer.manage.newName")}
            initial={name}
            siblings={childNames(allPaths, parent).filter((n) => n !== name)}
            confirmLabel={t("explorer.manage.rename")}
            pendingLabel={t("explorer.manage.renaming")}
            pending={move.isPending}
            onClose={close}
            onSubmit={(next) => runMove(planRenameFile(allFiles, file, next), close)}
          />
        );
        break;
      }
      case "renameFolder": {
        const { path } = dialog;
        const name = baseName(path);
        dialogs = (
          <NameDialog
            title={t("explorer.manage.renameFolder", { name })}
            description={t("explorer.manage.renameFolderHint")}
            label={t("explorer.manage.newName")}
            initial={name}
            siblings={childNames(allPaths, dirName(path)).filter((n) => n !== name)}
            confirmLabel={t("explorer.manage.rename")}
            pendingLabel={t("explorer.manage.renaming")}
            pending={move.isPending}
            onClose={close}
            onSubmit={(next) => runMove(planRenameFolder(allFiles, path, next), close)}
          />
        );
        break;
      }
      case "newFolder":
        dialogs = (
          <NameDialog
            title={t("explorer.manage.newFolder")}
            description={t("explorer.manage.newFolderHint")}
            label={t("explorer.manage.folderName")}
            initial=""
            siblings={childNames(allPaths, currentFolder)}
            confirmLabel={t("explorer.manage.create")}
            pendingLabel={t("explorer.manage.create")}
            onClose={close}
            onSubmit={(name) => {
              const path = joinPath(currentFolder, name);
              setExtraFolders((prev) => (prev.includes(path) ? prev : [...prev, path]));
              close();
              setCurrentFolder(path);
            }}
          />
        );
        break;
      case "move":
        dialogs = (
          <MoveDialog
            items={dialog.items}
            allFiles={allFiles}
            tree={tree}
            startFolder={currentFolder}
            pending={move.isPending}
            onClose={close}
            onSubmit={(dest) => runMove(planMoveInto(allFiles, dialog.items, dest), close)}
          />
        );
        break;
      case "delete":
        dialogs = (
          <ConfirmDialog
            open
            onOpenChange={(open) => !open && close()}
            title={t("explorer.manage.deleteTitle", { count: dialog.ids.length })}
            description={t("explorer.manage.deleteDescription", { count: dialog.ids.length })}
            confirmLabel={t("explorer.manage.deleteConfirm", { count: dialog.ids.length })}
            cancelLabel={t("explorer.manage.cancel")}
            destructive
            onConfirm={() => {
              const ids = dialog.ids;
              del.mutate(ids, {
                onSuccess: (res) => {
                  selection.clear();
                  if (res.failed === 0) {
                    toast.success(t("explorer.manage.deleted", { count: res.ok }));
                  } else {
                    toast.error(
                      t("explorer.manage.deletePartial", {
                        count: res.failed,
                        failed: res.failed,
                        total: ids.length,
                        reason: errMsg(res.firstError, t("explorer.manage.deleteFailed")),
                      }),
                    );
                  }
                },
                onError: (e) => toast.error(errMsg(e, t("explorer.manage.deleteFailed"))),
              });
            }}
          />
        );
        break;
    }
  }

  return {
    fileActions,
    folderActions,
    newFolderButton,
    dialogs,
    busy,
    selectionMove: () => setDialog({ kind: "move", items: selected }),
    selectionDelete: () => requestDelete(selected),
    onDragStartFile,
    onDragStartFolder,
    dropProps,
  };
}
