/**
 * Read-only file explorer for a model's current revision: folder-tree sidebar
 * + breadcrumbs, type chips, search/sort/layout controls, tag filter, upload
 * dropzone, Combine/Arrange bulk bar and a side preview pane for docs.
 *
 * PR 4 (rename/move/new folder/bulk delete) plugs in through `fileActions`,
 * `folderActions`, `toolbarExtra`, the `ExplorerContext` (current folder,
 * selection) and the prefixed selection keys (`fileKey`/`folderKey`).
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  ChevronRightIcon,
  CombineIcon,
  FolderInputIcon,
  Trash2Icon,
  LayoutGridIcon,
  ListIcon,
  PanelLeftIcon,
  PlusIcon,
  SearchIcon,
  XIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { modelQueryOptions } from "@/api/library";
import { useAuth } from "@/api/auth";
import { CombineDialog } from "@/components/model-detail/CombineDialog";
import { DocPreview } from "@/components/model-detail/explorer/DocPreview";
import { ExplorerContext, type ExplorerContextValue } from "@/components/model-detail/explorer/explorerContext";
import { FileEntry, FolderEntry } from "@/components/model-detail/explorer/FileEntry";
import { isCombinable } from "@/components/model-detail/explorer/fileMeta";
import { useExplorerManage } from "@/components/model-detail/explorer/useExplorerManage";
import { FolderTree } from "@/components/model-detail/explorer/FolderTree";
import { fileKey, folderKey, useSelection } from "@/components/model-detail/explorer/selection";
import {
  TYPE_GROUPS,
  applyFilters,
  applySearch,
  baseName,
  breadcrumbs,
  buildTree,
  countByGroup,
  findNode,
  flattenFiles,
  sortFiles,
  type SortKey,
  type TypeFilter,
} from "@/components/model-detail/explorer/tree";
import { pruneEmptyFolders, withEmptyFolders } from "@/components/model-detail/explorer/treeEdit";
import { useViewSettings } from "@/components/model-detail/explorer/viewSettings";
import { canArrange } from "@/components/plate/arrangeable";
import { PlateEditorDialog } from "@/components/plate/PlateEditorDialog";
import { TagFilter } from "@/components/tags/TagFilter";
import { EMPTY_TAG_FILTER, type TagFilterValue } from "@/components/tags/tagFilter";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { UploadDropzone, type UploadTarget } from "@/components/upload/UploadDropzone";
import { cn } from "@/lib/utils";
import type { FileOut, ModelDetail } from "@/api/types";

const SORT_KEYS: SortKey[] = ["name", "size", "mtime", "type"];

/** Keyed by model so folder/selection/client-only-folder state never leaks across models. */
export function FileExplorer(props: FileExplorerProps) {
  return <FileExplorerInner key={props.model.id} {...props} />;
}

interface FileExplorerProps {
  model: ModelDetail;
  /** Studio hand-off: jumps the viewer above to this file. Optional so the
   * explorer still renders standalone. */
  onViewIn3D?: (file: FileOut) => void;
  /** Extra per-file row actions. */
  fileActions?: (file: FileOut) => ReactNode;
  /** Extra per-folder actions, shown on folder entries and tree nodes. */
  folderActions?: (path: string) => ReactNode;
  /** Extra toolbar controls. */
  toolbarExtra?: ReactNode;
}

function FileExplorerInner({
  model,
  onViewIn3D,
  fileActions,
  folderActions,
  toolbarExtra,
}: FileExplorerProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { data: me, can } = useAuth();
  // The upload API is owner-or-admin only (NULL-creator models: admin only).
  const canUpload = can("admin") || (!!me && model.created_by_user_id === me.id);

  const [view, updateView] = useViewSettings();
  const selection = useSelection();
  const [folder, setFolder] = useState("");
  const [query, setQuery] = useState("");
  const [type, setType] = useState<TypeFilter>("all");
  const [tagFilter, setTagFilter] = useState<TagFilterValue>(EMPTY_TAG_FILTER);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [previewId, setPreviewId] = useState<number | null>(null);
  const [showAddFiles, setShowAddFiles] = useState(false);
  const [showCombine, setShowCombine] = useState(false);
  const [arrangeFiles, setArrangeFiles] = useState<FileOut[] | null>(null);
  // Folders created in the UI exist only here until a file lands in them.
  const [extraFolders, setExtraFolders] = useState<string[]>([]);

  const currentRevision = model.current_revision;
  const allFiles = useMemo(() => currentRevision?.files ?? [], [currentRevision]);
  const realTree = useMemo(() => buildTree(allFiles), [allFiles]);
  const fullTree = useMemo(() => withEmptyFolders(buildTree(allFiles), extraFolders), [allFiles, extraFolders]);
  useEffect(() => {
    setExtraFolders((prev) => {
      const next = pruneEmptyFolders(realTree, prev);
      return next.length === prev.length ? prev : next;
    });
  }, [realTree]);
  // A folder that vanished (file deleted/moved) falls back to the root.
  const current = findNode(fullTree, folder) ? folder : "";
  const scope = findNode(fullTree, current) ?? fullTree;

  // A search or a specific type chip lists matches flat across the whole subtree;
  // plain "All" keeps the folder view (subfolders + direct files).
  const flat = query.trim() !== "" || type !== "all";
  const scopeFiles = useMemo(() => flattenFiles(scope), [scope]);
  // Chip counts always cover the subtree, i.e. what clicking the chip lists.
  const counts = useMemo(() => countByGroup(applySearch(scopeFiles, { query, tagFilter })), [scopeFiles, query, tagFilter]);
  const matched = useMemo(() => applyFilters(scopeFiles, { query, tagFilter, type }), [scopeFiles, query, tagFilter, type]);

  // Search results are flat across the subtree; otherwise show this folder's
  // direct subfolders and files (only those that still contain a match).
  const plainView = !flat && tagFilter.tags.length === 0;
  const shownTree = useMemo(
    () => findNode(plainView ? withEmptyFolders(buildTree(matched), extraFolders) : buildTree(matched), current),
    [matched, current, plainView, extraFolders],
  );
  const shownFiles = useMemo(
    () => sortFiles(flat ? matched : (shownTree?.files ?? []), view.sort, view.dir),
    [flat, matched, shownTree, view.sort, view.dir],
  );
  const shownFolders = useMemo(() => {
    if (flat) return [];
    const folders = [...(shownTree?.folders ?? [])];
    return view.sort === "name" && view.dir === "desc" ? folders.reverse() : folders;
  }, [flat, shownTree, view.sort, view.dir]);

  const previewFile = previewId === null ? null : (allFiles.find((file) => file.id === previewId) ?? null);
  // Bulk actions only ever touch files that are currently visible.
  const selectedCount = selection.keys.size;
  const selectedFiles = useMemo(
    () => shownFiles.filter((file) => selection.has(fileKey(file.id)) && isCombinable(file)),
    [shownFiles, selection],
  );

  // Selection never outlives visibility: drop ids hidden by a new folder,
  // search, chip or tag filter.
  const { retain, clear: clearSelection, toggle: toggleKey } = selection;
  useEffect(() => {
    retain(new Set([...shownFiles.map((file) => fileKey(file.id)), ...shownFolders.map((node) => folderKey(node.path))]));
  }, [shownFiles, shownFolders, retain]);

  const previewRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (previewId === null) return;
    const el = previewRef.current;
    el?.scrollIntoView?.({ block: "nearest" });
    el?.focus({ preventScroll: true });
  }, [previewId]);

  const navigate = useCallback(
    (path: string) => {
      setFolder(path);
      setSidebarOpen(false);
      setQuery("");
      clearSelection();
    },
    [clearSelection],
  );
  const toggleFolderSelected = useCallback((path: string, on: boolean) => toggleKey(folderKey(path), on), [toggleKey]);
  const toggleSelected = useCallback((file: FileOut, on: boolean) => toggleKey(fileKey(file.id), on), [toggleKey]);
  const togglePreview = useCallback((file: FileOut) => setPreviewId((prev) => (prev === file.id ? null : file.id)), []);
  const arrangeOne = useCallback((file: FileOut) => setArrangeFiles([file]), []);

  // Uploads always target the model's CURRENT revision through the existing
  // `PUT /api/uploads` seam -- never `create_revision` (a costly full
  // snapshot-copy that 409s mid-upload).
  async function resolveTarget(): Promise<UploadTarget | null> {
    if (!currentRevision) return null;
    return { modelId: model.id, revisionId: currentRevision.id };
  }

  function handleUploadComplete() {
    // The gallery-only `["models"]` key wouldn't refresh THIS already-open
    // detail page -- invalidate the specific model-detail query instead.
    void queryClient.invalidateQueries({ queryKey: modelQueryOptions(model.slug).queryKey });
    void queryClient.invalidateQueries({ queryKey: ["storage"] });
  }

  const ctx = useMemo<ExplorerContextValue>(
    () => ({ model, canUpload, currentFolder: current, setCurrentFolder: navigate, selection }),
    [model, canUpload, current, navigate, selection],
  );
  const manage = useExplorerManage({
    model,
    canUpload,
    allFiles,
    tree: fullTree,
    extraFolders,
    setExtraFolders,
    currentFolder: current,
    setCurrentFolder: navigate,
    selection,
  });
  const { onDragStartFile } = manage;
  const { fileActions: manageFileActions, folderActions: manageFolderActions } = manage;
  const rowActions = useCallback(
    (file: FileOut) => (
      <>
        {manageFileActions(file)}
        {fileActions?.(file)}
      </>
    ),
    [manageFileActions, fileActions],
  );
  const folderRowActions = useCallback(
    (path: string) => (
      <>
        {manageFolderActions(path)}
        {folderActions?.(path)}
      </>
    ),
    [manageFolderActions, folderActions],
  );
  const trail = breadcrumbs(current);
  const newFolderEmpty = extraFolders.includes(current) && scope.total === 0;
  const rootLabel = t("explorer.root");
  const typeLabel = (key: TypeFilter) => t(`explorer.type.${key}`);

  return (
    <ExplorerContext.Provider value={ctx}>
      <div className="@container/explorer space-y-3">
        {currentRevision ? (
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-40 flex-1">
              <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t("explorer.search")}
                aria-label={t("explorer.search")}
                className="pl-8"
              />
            </div>
            {allFiles.length > 0 ? (
              <TagFilter value={tagFilter} onChange={setTagFilter} label={t("explorer.filterByTag")} countKey="model_files" />
            ) : null}
            <Select value={view.sort} onValueChange={(value) => updateView({ sort: value as SortKey })}>
              <SelectTrigger size="sm" aria-label={t("explorer.sortBy")} className="w-28">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SORT_KEYS.map((key) => (
                  <SelectItem key={key} value={key}>
                    {t(`explorer.sort.${key}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              aria-label={t(view.dir === "asc" ? "explorer.ascending" : "explorer.descending")}
              onClick={() => updateView({ dir: view.dir === "asc" ? "desc" : "asc" })}
            >
              {view.dir === "asc" ? <ArrowUpIcon className="size-4" /> : <ArrowDownIcon className="size-4" />}
            </Button>
            <div role="group" aria-label={t("explorer.layout")} className="flex gap-1">
              <Button
                type="button"
                size="icon-sm"
                variant={view.layout === "list" ? "secondary" : "ghost"}
                aria-label={t("explorer.listView")}
                aria-pressed={view.layout === "list"}
                onClick={() => updateView({ layout: "list" })}
              >
                <ListIcon className="size-4" />
              </Button>
              <Button
                type="button"
                size="icon-sm"
                variant={view.layout === "grid" ? "secondary" : "ghost"}
                aria-label={t("explorer.gridView")}
                aria-pressed={view.layout === "grid"}
                onClick={() => updateView({ layout: "grid" })}
              >
                <LayoutGridIcon className="size-4" />
              </Button>
            </div>
            {manage.newFolderButton}
            {toolbarExtra}
            {canUpload ? (
              <Button type="button" variant="outline" size="sm" onClick={() => setShowAddFiles((prev) => !prev)}>
                <PlusIcon className="size-4" />
                {t("explorer.addFiles")}
              </Button>
            ) : null}
          </div>
        ) : null}

        {allFiles.length > 0 ? (
          <div role="group" aria-label={t("explorer.fileTypes")} className="flex flex-wrap gap-1.5">
            {(["all", ...TYPE_GROUPS] as TypeFilter[]).map((key) => (
              <Button
                key={key}
                type="button"
                size="sm"
                variant={type === key ? "secondary" : "outline"}
                aria-pressed={type === key}
                className={cn("h-7 rounded-full px-3 text-xs", type === key && "border-primary/50 text-primary")}
                onClick={() => setType(key)}
              >
                {typeLabel(key)}
                <span className="text-muted-foreground tabular-nums">{counts[key]}</span>
              </Button>
            ))}
          </div>
        ) : null}

        {canUpload && showAddFiles && currentRevision ? (
          <div className="space-y-1.5">
            <p className="text-xs text-muted-foreground">
              {t("explorer.uploadInto", { path: current === "" ? rootLabel : current })}
            </p>
            <UploadDropzone resolveTarget={resolveTarget} onUploadComplete={handleUploadComplete} relPathPrefix={current} />
          </div>
        ) : null}

        {manage.busy ? (
          <p role="status" className="text-sm text-muted-foreground">
            {t("explorer.busy")}
          </p>
        ) : null}

        {canUpload && selectedCount > 0 ? (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border bg-muted/40 px-3 py-2 text-sm">
            <span>{t("explorer.selected", { count: selectedCount })}</span>
            <div className="flex flex-wrap items-center gap-2">
              {selectedFiles.length > 0 ? (
                <Button type="button" size="sm" onClick={() => setShowCombine(true)}>
                  <CombineIcon className="size-4" />
                  {t("explorer.combine")}
                </Button>
              ) : null}
              {selectedFiles.some(canArrange) ? (
                <Button type="button" size="sm" variant="outline" onClick={() => setArrangeFiles(selectedFiles.filter(canArrange))}>
                  <LayoutGridIcon className="size-4" />
                  {t("explorer.arrange")}
                </Button>
              ) : null}
              <Button type="button" size="sm" variant="outline" disabled={manage.busy} onClick={manage.selectionMove}>
                <FolderInputIcon className="size-4" />
                {t("explorer.manage.move")}
              </Button>
              <Button type="button" size="sm" variant="outline" disabled={manage.busy} onClick={manage.selectionDelete}>
                <Trash2Icon className="size-4" />
                {t("explorer.manage.delete")}
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={clearSelection}>
                {t("explorer.clear")}
              </Button>
            </div>
          </div>
        ) : null}

        {allFiles.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">{t("explorer.noFiles")}</p>
        ) : (
          <div className="grid gap-3 @2xl/explorer:grid-cols-[13rem_minmax(0,1fr)]">
            <aside
              aria-label={t("explorer.folders")}
              className={cn("min-w-0 rounded-lg border bg-card/50 p-1.5 @2xl/explorer:block @2xl/explorer:max-h-[32rem] @2xl/explorer:overflow-y-auto", !sidebarOpen && "hidden")}
            >
              <FolderTree
                root={fullTree}
                current={current}
                onSelect={navigate}
                rootLabel={rootLabel}
                folderSlot={folderRowActions}
                dropProps={manage.dropProps}
              />
            </aside>

            <div className="min-w-0 space-y-3">
              <div className="flex items-center gap-1">
                <Button
                  type="button"
                  size="icon-sm"
                  variant={sidebarOpen ? "secondary" : "ghost"}
                  className="@2xl/explorer:hidden"
                  aria-label={t("explorer.toggleFolders")}
                  aria-pressed={sidebarOpen}
                  onClick={() => setSidebarOpen((open) => !open)}
                >
                  <PanelLeftIcon className="size-4" />
                </Button>
                <nav aria-label={t("explorer.path")} className="flex min-w-0 flex-1 flex-wrap items-center gap-1 text-sm">
                  {trail.map((crumb, index) => (
                    <span key={crumb.path} className="flex min-w-0 max-w-full items-center gap-1">
                      {index > 0 ? <ChevronRightIcon className="size-3.5 text-muted-foreground" /> : null}
                      <button
                        type="button"
                        {...manage.dropProps(crumb.path)}
                        onClick={() => navigate(crumb.path)}
                        aria-current={index === trail.length - 1 ? "page" : undefined}
                        title={crumb.name || rootLabel}
                        className="max-w-full truncate rounded px-1.5 py-0.5 text-muted-foreground hover:bg-muted hover:text-foreground aria-current:font-medium aria-current:text-foreground data-[drop-over=true]:bg-primary/15 data-[drop-over=true]:ring-1 data-[drop-over=true]:ring-primary/50"
                      >
                        {crumb.name || rootLabel}
                      </button>
                    </span>
                  ))}
                </nav>
              </div>

              {shownFiles.length === 0 && shownFolders.length === 0 ? (
                <p className="py-8 text-center text-sm text-muted-foreground">
                  {query || type !== "all" || tagFilter.tags.length > 0
                    ? t("explorer.noMatch")
                    : newFolderEmpty
                      ? t("explorer.emptyNewFolder")
                      : t("explorer.emptyFolder")}
                </p>
              ) : (
                <ul
                  role="list"
                  className={cn(
                    view.layout === "grid"
                      ? "grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-3"
                      : "@container/files divide-y",
                  )}
                >
                  {shownFolders.map((node) => (
                    <FolderEntry
                      key={node.path}
                      node={node}
                      layout={view.layout}
                      onOpen={navigate}
                      actionsSlot={folderRowActions(node.path)}
                      selectable={canUpload}
                      selected={selection.has(folderKey(node.path))}
                      onSelectedChange={toggleFolderSelected}
                      onDragStartFolder={canUpload ? manage.onDragStartFolder : undefined}
                      dropProps={manage.dropProps}
                    />
                  ))}
                  {shownFiles.map((file) => {
                    return (
                      <FileEntry
                        key={file.id}
                        file={file}
                        model={model}
                        layout={view.layout}
                        label={flat ? file.rel_path : baseName(file.rel_path)}
                        selectable={canUpload}
                        selected={selection.has(fileKey(file.id))}
                        onSelectedChange={toggleSelected}
                        previewOpen={previewId === file.id}
                        onTogglePreview={togglePreview}
                        onViewIn3D={onViewIn3D}
                        onArrange={canUpload ? arrangeOne : undefined}
                        renderActions={rowActions}
                        onDragStartFile={canUpload ? onDragStartFile : undefined}
                      />
                    );
                  })}
                </ul>
              )}

              {previewFile ? (
                <section
                  ref={previewRef}
                  tabIndex={-1}
                  aria-label={t("explorer.previewTitle", { name: baseName(previewFile.rel_path) })}
                  className="rounded-lg border bg-card p-3 outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <p className="min-w-0 truncate font-mono text-xs" title={previewFile.rel_path}>
                      {previewFile.rel_path}
                    </p>
                    <Button type="button" size="icon-sm" variant="ghost" aria-label={t("explorer.closePreview")} onClick={() => setPreviewId(null)}>
                      <XIcon className="size-4" />
                    </Button>
                  </div>
                  <div data-testid={`doc-preview-${previewFile.id}`} className="[overflow-wrap:anywhere]">
                    <DocPreview file={previewFile} />
                  </div>
                </section>
              ) : null}
            </div>
          </div>
        )}

        {manage.dialogs}

        {arrangeFiles ? (
          <PlateEditorDialog
            open
            onOpenChange={(open) => {
              if (!open) {
                setArrangeFiles(null);
                selection.clear();
              }
            }}
            slug={model.slug}
            files={arrangeFiles}
          />
        ) : null}

        {showCombine ? (
          <CombineDialog
            open
            onOpenChange={(open) => {
              setShowCombine(open);
              if (!open) selection.clear();
            }}
            slug={model.slug}
            files={selectedFiles}
          />
        ) : null}
      </div>
    </ExplorerContext.Provider>
  );
}
