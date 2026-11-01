/** Rename / move / new folder / bulk delete / drag-and-drop for the explorer (all gated on `canUpload`). */
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type HTMLAttributes, type ReactNode } from "react";
import { FolderPlusIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  MoveStillRunning,
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
import { ManageMenu } from "@/components/model-detail/explorer/ManageMenu";
import { MoveDialog, NameDialog } from "@/components/model-detail/explorer/manageDialogs";
import type { ExplorerSelection } from "@/components/model-detail/explorer/selection";
import { baseName, dirName, findNode, flattenFiles, isInside, type TreeNode } from "@/components/model-detail/explorer/tree";
import { childNames, joinPath, rewriteFolders } from "@/components/model-detail/explorer/treeEdit";
import { Button } from "@/components/ui/button";
import type { FileOut, ModelDetail } from "@/api/types";

type Dialog =
  | { kind: "renameFile"; file: FileOut }
  | { kind: "renameFolder"; path: string }
  | { kind: "move"; items: DragItems }
  | { kind: "newFolder" }
  | { kind: "delete"; ids: number[]; folders: string[] };

const errMsg = (e: unknown, fallback: string) =>
  e instanceof ApiError ? e.detail : e instanceof Error && e.message ? e.message : fallback;

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
  const { mutate: moveMutate, isPending: movePending } = useMoveFiles(model);
  const { mutate: deleteMutate, isPending: deletePending } = useDeleteFiles(model.slug);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const busy = movePending || deletePending;
  const close = useCallback(() => setDialog(null), []);
  const clearSelection = selection.clear;
  const hasKey = selection.has;

  // Drag handlers read the latest selection through refs so they stay referentially stable
  // (selection changes must not re-render every memoized row).
  const selectionRef = useRef({ keys: selection.keys, has: hasKey });
  const pendingRef = useRef(movePending);
  useEffect(() => {
    selectionRef.current = { keys: selection.keys, has: hasKey };
    pendingRef.current = movePending;
  });

  const allPaths = useMemo(() => [...allFiles.map((f) => f.rel_path), ...extraFolders], [allFiles, extraFolders]);

  const filesUnder = useCallback(
    (folder: string) => {
      const node = findNode(tree, folder);
      return node ? flattenFiles(node) : [];
    },
    [tree],
  );

  /** Applies the client-side half of a finished (or still-running) move: client-only folders and the open folder follow. */
  const applyLocal = useCallback(
    (plan: MovePlan) => {
      // The open folder follows its rename/move; it is kept as a placeholder until the refetch shows it.
      const followed = plan.folderMoves
        .filter((m) => isInside(currentFolder, m.from))
        .map((m) => rewriteFolders([currentFolder], m.from, m.to)[0]);
      setExtraFolders((prev) => {
        const next = plan.folderMoves.reduce((acc, m) => rewriteFolders(acc, m.from, m.to), prev);
        return [...next, ...followed.filter((p) => !next.includes(p))];
      });
      if (followed.length > 0) setCurrentFolder(followed[0]);
      clearSelection();
    },
    [clearSelection, currentFolder, setCurrentFolder, setExtraFolders],
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
      if (plan.moves.length === 0) {
        applyLocal(plan);
        after?.();
        return;
      }
      moveMutate(plan.moves, {
        onSuccess: () => {
          applyLocal(plan);
          toast.success(t("explorer.manage.moved", { count: plan.moves.length }));
          after?.();
        },
        onError: (e) => {
          if (e instanceof MoveStillRunning) {
            // The job keeps running server-side: treat it as in flight, not failed.
            applyLocal(plan);
            toast.info(t(e.remaining > 0 ? "explorer.manage.moveSlowPartial" : "explorer.manage.moveSlow"));
            after?.();
            return;
          }
          toast.error(errMsg(e, t("explorer.manage.moveFailed")));
        },
      });
    },
    [applyLocal, moveMutate, t],
  );

  const dropOn = useCallback(
    (dest: string, items: DragItems) => {
      if (pendingRef.current) return;
      runMove(planMoveInto(allFiles, items, dest));
    },
    [allFiles, runMove],
  );

  // The drop highlight is toggled on the DOM node itself, so hovering never re-renders the explorer.
  const dropProps = useCallback(
    (path: string): HTMLAttributes<HTMLElement> => {
      if (!canUpload) return {};
      return {
        onDragOver: (event) => {
          if (!isExplorerDrag(event.dataTransfer)) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = "move";
          event.currentTarget.dataset.dropOver = "true";
        },
        onDragLeave: (event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) delete event.currentTarget.dataset.dropOver;
        },
        onDrop: (event) => {
          delete event.currentTarget.dataset.dropOver;
          if (!isExplorerDrag(event.dataTransfer)) return;
          event.preventDefault();
          event.stopPropagation();
          const items = decodeDrag(event.dataTransfer.getData(DND_TYPE));
          if (items) dropOn(path, items);
        },
      };
    },
    [canUpload, dropOn],
  );

  const startDrag = useCallback((event: DragEvent, item: Parameters<typeof dragItemsFor>[0]) => {
    const { keys, has } = selectionRef.current;
    const items = dragItemsFor(item, itemsFromKeys(keys), has);
    event.dataTransfer.setData(DND_TYPE, encodeDrag(items));
    event.dataTransfer.effectAllowed = "move";
  }, []);
  const onDragStartFile = useCallback((event: DragEvent, file: FileOut) => startDrag(event, { kind: "file", id: file.id }), [startDrag]);
  const onDragStartFolder = useCallback((event: DragEvent, path: string) => startDrag(event, { kind: "folder", path }), [startDrag]);

  const requestDelete = useCallback(
    (items: DragItems) => {
      const ids = new Set(items.fileIds);
      for (const folder of items.folders) for (const file of filesUnder(folder)) ids.add(file.id);
      if (ids.size === 0) {
        // Only client-only empty folders: nothing server-side to delete.
        setExtraFolders((prev) => prev.filter((p) => !items.folders.some((f) => isInside(p, f))));
        clearSelection();
        return;
      }
      setDialog({ kind: "delete", ids: [...ids], folders: items.folders });
    },
    [clearSelection, filesUnder, setExtraFolders],
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
          busy={busy}
          onRename={() => setDialog({ kind: "renameFolder", path })}
          onMove={() => setDialog({ kind: "move", items: { fileIds: [], folders: [path] } })}
          onDelete={() => requestDelete({ fileIds: [], folders: [path] })}
        />
      ) : null,
    [busy, canUpload, requestDelete],
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
        const name = baseName(file.rel_path);
        dialogs = (
          <NameDialog
            title={t("explorer.manage.renameFile", { name })}
            label={t("explorer.manage.newName")}
            initial={name}
            siblings={childNames(allPaths, dirName(file.rel_path)).filter((n) => n !== name)}
            confirmLabel={t("explorer.manage.rename")}
            pendingLabel={t("explorer.manage.renaming")}
            pending={movePending}
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
            pending={movePending}
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
            pending={movePending}
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
              const { ids, folders } = dialog;
              deleteMutate(ids, {
                onSuccess: (res) => {
                  clearSelection();
                  // Client-only folders inside a deleted folder go with it.
                  if (folders.length > 0) setExtraFolders((prev) => prev.filter((p) => !folders.some((f) => isInside(p, f))));
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

  const selectionMove = useCallback(
    () => setDialog({ kind: "move", items: itemsFromKeys(selectionRef.current.keys) }),
    [],
  );
  const selectionDelete = useCallback(() => requestDelete(itemsFromKeys(selectionRef.current.keys)), [requestDelete]);

  // The callbacks are referentially stable; consumers destructure them so the object itself needn't be.
  return {
    fileActions,
    folderActions,
    newFolderButton,
    dialogs,
    busy,
    selectionMove,
    selectionDelete,
    onDragStartFile,
    onDragStartFolder,
    dropProps,
  };
}
