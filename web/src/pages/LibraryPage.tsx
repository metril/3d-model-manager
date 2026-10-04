import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useWindowVirtualizer } from "@tanstack/react-virtual";
import {
  ArchiveIcon,
  BookmarkIcon,
  CheckCircle2Icon,
  CheckSquareIcon,
  FileIcon,
  FolderInputIcon,
  FolderTreeIcon,
  LayoutGridIcon,
  ListIcon,
  ListPlusIcon,
  PlusIcon,
  RefreshCwIcon,
  SearchIcon,
  StarIcon,
  TagIcon,
  Trash2Icon,
  UploadCloudIcon,
  XIcon,
} from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";

import { useCategories } from "@/api/categories";
import { useFollowedCollections } from "@/api/collections";
import {
  useBulkDeleteModels,
  useBulkUpdateModels,
  useCreateModel,
  useMergeModels,
  useModelsQuery,
  useTags,
} from "@/api/library";
import { useProjects } from "@/api/projects";
import { useEnqueueModel } from "@/api/queue";
import { useTriggerScan } from "@/api/scan";
import { uploadFilesWithDuplicateHandling } from "@/lib/uploadHelper";
import { ApiError } from "@/api/client";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { FolderBrowser } from "@/components/gallery/FolderBrowser";
import { ModelCard } from "@/components/gallery/ModelCard";
import { ModelRow } from "@/components/gallery/ModelRow";
import { NewModelDialog } from "@/components/gallery/NewModelDialog";
import { ProjectFolderView } from "@/components/gallery/ProjectFolderView";
import { getProjectIcon } from "@/lib/projectIcons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useHotkeys } from "@/hooks/useHotkeys";
import { chunkIntoRows, columnsForWidth, estimateListRowHeight, estimateRowHeight } from "@/lib/grid";
import { useDebouncedValue } from "@/lib/format";
import {
  BLOB_FORMATS,
  type BlobFormat,
  type CategoryOut,
  type FollowedCollection,
  type ModelSummary,
  type PrintStatus,
  type ProjectOut,
} from "@/api/types";
import { FORMAT_LABELS } from "@/lib/formatMeta";
import { ALL_PRINT_STATUSES, getPrintStatusMeta } from "@/lib/printStatus";
import { tagColorClass } from "@/lib/tagColors";
import { cn } from "@/lib/utils";
import type { LibrarySearch } from "@/pages/librarySearch";

const SORT_OPTIONS = [
  { value: "-updated_at", label: "Recently updated" },
  { value: "-created_at", label: "Recently added" },
  { value: "-print_count", label: "Most printed" },
  { value: "name", label: "Name" },
] as const;

// Stable fallbacks so `data ?? []` does not hand `useMemo` deps a fresh array every render.
const EMPTY_PROJECTS: ProjectOut[] = [];
const EMPTY_COLLECTIONS: FollowedCollection[] = [];
const EMPTY_CATEGORIES: CategoryOut[] = [];

type ViewMode = "grid" | "list" | "folders";
const VIEW_MODE_KEY = "library-view";

function loadViewMode(): ViewMode {
  try {
    const stored = window.localStorage.getItem(VIEW_MODE_KEY);
    if (stored === "grid" || stored === "list" || stored === "folders") return stored;
  } catch {
    // Private browsing / disabled storage -- fall back to the default below.
  }
  return "grid";
}

function saveViewMode(mode: ViewMode): void {
  try {
    window.localStorage.setItem(VIEW_MODE_KEY, mode);
  } catch {
    // Nothing to persist to -- the toggle still works for this session.
  }
}

/** Grid / list / folders segmented control (R13b), persisted to
 * localStorage so the choice survives a reload. */
function ViewModeToggle({ value, onChange }: { value: ViewMode; onChange: (mode: ViewMode) => void }) {
  const options: Array<{ mode: ViewMode; label: string; icon: typeof LayoutGridIcon }> = [
    { mode: "grid", label: "Grid view", icon: LayoutGridIcon },
    { mode: "list", label: "List view", icon: ListIcon },
    { mode: "folders", label: "Folder view", icon: FolderTreeIcon },
  ];
  return (
    <div role="group" aria-label="View mode" className="flex items-center gap-0.5 rounded-lg border border-border p-0.5">
      {options.map((option) => (
        <Button
          key={option.mode}
          type="button"
          variant={value === option.mode ? "secondary" : "ghost"}
          size="icon-sm"
          aria-label={option.label}
          aria-pressed={value === option.mode}
          onClick={() => onChange(option.mode)}
        >
          <option.icon />
        </Button>
      ))}
    </div>
  );
}

/** A single-select filter chip (used for the format facet). Rendered as a
 * button wrapping a Badge so it keeps an accessible name + `aria-pressed`. */
function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button type="button" aria-pressed={active} onClick={onClick} className="rounded-4xl">
      <Badge variant={active ? "default" : "outline"} className="cursor-pointer">
        {children}
      </Badge>
    </button>
  );
}

export function LibraryPage() {
  // One-way seed only: a provenance badge or a related-models card can deep
  // link here with `?collection=<id>` (see `librarySearch.ts`), but the
  // facet's own selections never write back to the URL -- same as every
  // other filter on this page.
  const search = useSearch({ strict: false }) as LibrarySearch;
  const navigate = useNavigate();

  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const [searchInput, setSearchInput] = useState("");
  const debouncedSearch = useDebouncedValue(searchInput, 300);
  const [activeTag, setActiveTag] = useState<string | undefined>(undefined);
  // The backend's `format` filter accepts a single value (SPEC "API surface"),
  // so the chips behave as a single-select facet ("All" clears it).
  const [activeFormat, setActiveFormat] = useState<BlobFormat | undefined>(undefined);
  const [slicedOnly, setSlicedOnly] = useState(false);
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [archivedOnly, setArchivedOnly] = useState(false);
  const [activeCollection, setActiveCollection] = useState<number | undefined>(search.collection);
  // R13b: unlike `activeCollection` above (one-way-seed-only, local state),
  // this facet is derived LIVE from `search.category` every render -- a
  // sidebar category link clicked while already on `/` only changes the
  // search params (same route), so a one-shot `useState` seed would never
  // see the update. Setting it writes back to the URL (`goToCategory`
  // below) rather than local state, which is what makes the sidebar's own
  // links (and the browser back button) agree with these chips.
  const activeCategory = search.category !== undefined ? Number(search.category) : undefined;
  const activeProject = search.project !== undefined ? Number(search.project) : undefined;
  const activePrintStatus = search.print_status;

  function goToCategory(next: number | undefined) {
    void navigate({ to: "/", search: (prev: LibrarySearch) => ({ ...prev, category: next }) });
  }

  function goToProject(next: number | undefined) {
    void navigate({ to: "/", search: (prev: LibrarySearch) => ({ ...prev, project: next }) });
  }

  function goToPrintStatus(next: string | undefined) {
    void navigate({ to: "/", search: (prev: LibrarySearch) => ({ ...prev, print_status: next }) });
  }
  const [sort, setSort] = useState<string>("-updated_at");
  const [viewMode, setViewMode] = useState<ViewMode>(loadViewMode);
  // Folder view's current directory DOES live in the URL bidirectionally
  // (unlike the one-way-seed facets above) -- breadcrumbs and deep links
  // need it round-tripped, so it's read straight from `search` rather than
  // mirrored into local state.
  const path = search.path ?? "";

  function handleViewModeChange(mode: ViewMode) {
    setViewMode(mode);
    saveViewMode(mode);
  }

  function goToPath(nextPath: string) {
    void navigate({ to: "/", search: (prev: LibrarySearch) => ({ ...prev, path: nextPath || undefined }) });
  }

  const triggerScan = useTriggerScan();
  function startScan() {
    triggerScan.mutate(undefined, {
      onSuccess: () =>
        toast.success("Scan started", {
          action: { label: "View jobs", onClick: () => void navigate({ to: "/jobs" }) },
        }),
    });
  }

  // Selection state: can be triggered via explicit "Sélectionner" button or
  // by modifier clicks / checkbox clicks.
  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  // R9-A item 6: the anchor for shift+click range selection -- the index of
  // the most recently (modified-)clicked card, cleared whenever selection is
  // exited so a later range doesn't reach back into a previous selection.
  const [lastSelectedIndex, setLastSelectedIndex] = useState<number | null>(null);
  // R9-C item 5: lifted here (rather than local to `SelectionActionBar`) so
  // the `Delete` hotkey -- fired from anywhere on the page, not just while
  // focus is inside the selection bar -- can open it.
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);

  // Model merge dialog state
  const mergeModels = useMergeModels();
  const [mergeConfirmState, setMergeConfirmState] = useState<{
    target: ModelSummary;
    sources: ModelSummary[];
  } | null>(null);

  function handleMergeModels(target: ModelSummary, sourceIds: number[]) {
    const sources = items.filter((m) => sourceIds.includes(m.id) && m.id !== target.id);
    if (sources.length === 0) return;
    setMergeConfirmState({ target, sources });
  }

  async function confirmMerge() {
    if (!mergeConfirmState) return;
    const { target, sources } = mergeConfirmState;
    try {
      await mergeModels.mutateAsync({
        targetSlug: target.slug,
        sourceSlugs: sources.map((s) => s.slug),
      });
      toast.success(`Fichiers fusionnés dans "${target.name}" avec succès !`);
      clearSelection();
    } catch {
      toast.error("Impossible de fusionner les modèles");
    } finally {
      setMergeConfirmState(null);
    }
  }

  function clearSelection() {
    setSelectedIds(new Set());
    setLastSelectedIndex(null);
    setSelectMode(false);
  }

  function toggleSelected(id: number, next: boolean) {
    setSelectedIds((prev) => {
      const updated = new Set(prev);
      if (next) updated.add(id);
      else updated.delete(id);
      if (updated.size === 0) {
        setSelectMode(false);
        setLastSelectedIndex(null);
      }
      return updated;
    });
  }

  /** Ctrl/Cmd toggles just this card; Shift selects the inclusive range from
   * `lastSelectedIndex` (or this index, if there isn't one yet) through this
   * index, adding to the existing selection rather than replacing it. */
  function handleModifiedClick(event: React.MouseEvent, index: number) {
    if (event.shiftKey) {
      const anchor = lastSelectedIndex !== null ? lastSelectedIndex : index;
      const [lo, hi] = anchor <= index ? [anchor, index] : [index, anchor];
      const rangeIds = items.slice(lo, hi + 1).map((model) => model.id);
      setSelectedIds((prev) => new Set([...prev, ...rangeIds]));
    } else {
      const model = items[index];
      if (model) toggleSelected(model.id, !selectedIds.has(model.id));
    }
    setLastSelectedIndex(index);
  }

  const tagsQuery = useTags();
  const collectionsQuery = useFollowedCollections();
  const collections = collectionsQuery.data ?? EMPTY_COLLECTIONS;
  const activeCollectionTitle = collections.find((collection) => collection.id === activeCollection)?.title;
  const categoriesQuery = useCategories();
  const categories = categoriesQuery.data ?? EMPTY_CATEGORIES;
  const projectsQuery = useProjects();
  const projects = projectsQuery.data ?? EMPTY_PROJECTS;

  const isSearching = Boolean(debouncedSearch.trim());
  // When not searching and no project folder is open, only show root models (project: 0).
  // When searching, search across all folders (project: undefined).
  // When inside a folder, show models in that folder (project: activeProject).
  const effectiveProject = activeProject !== undefined ? activeProject : (isSearching ? undefined : 0);

  const filters = useMemo(
    () => ({
      q: debouncedSearch || undefined,
      tag: activeTag,
      format: activeFormat,
      has_sliced: slicedOnly || undefined,
      collection: activeCollection,
      category: activeCategory,
      project: effectiveProject,
      print_status: activePrintStatus,
      favorite: favoritesOnly || undefined,
      archived: archivedOnly || undefined,
      sort,
    }),
    [
      debouncedSearch,
      activeTag,
      activeFormat,
      slicedOnly,
      favoritesOnly,
      archivedOnly,
      activeCollection,
      activeCategory,
      effectiveProject,
      activePrintStatus,
      sort,
    ],
  );

  const modelsQuery = useModelsQuery(filters);
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = modelsQuery;
  const items = useMemo(
    () => modelsQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [modelsQuery.data],
  );
  const selectedItems = useMemo(() => items.filter((model) => selectedIds.has(model.id)), [items, selectedIds]);

  function selectAll() {
    setSelectedIds(new Set(items.map((model) => model.id)));
  }

  // R9-C item 5: `/` and `Escape` always make sense; `a` only selects
  // everything while a selection is already active (otherwise a bare "a"
  // while typing in the search box would be indistinguishable from typing
  // an "a" -- the hook already guards inputs, but scoping this one to an
  // active selection too keeps it from firing over any other future
  // non-input surface). `mod+a` always selects all, `Escape` clears the
  // selection, and `Delete` opens the existing bulk-delete confirm.
  useHotkeys({
    "/": () => searchInputRef.current?.focus(),
    // Fix wave finding 5: Radix dialogs/popovers (ConfirmDialog,
    // NewModelDialog, the Tags/Collection popovers) already handle their own
    // Escape via `DismissableLayer` -- if one of those is open, this
    // document-level handler must NOT also fire, or dismissing e.g. the
    // bulk-delete confirm silently discards the whole selection underneath
    // it. Radix content renders `role="dialog"` for both `Dialog` and
    // `Popover` in this codebase (see `node_modules/@radix-ui/react-popover`)
    // with `data-state="open"` while mounted/open.
    Escape: (event) => {
      if (document.querySelector('[role="dialog"][data-state="open"]')) return;
      if (event.target instanceof Element && event.target.closest('[role="dialog"]')) return;
      clearSelection();
    },
    ...(selectedIds.size > 0 ? { a: () => selectAll() } : {}),
    "mod+a": () => selectAll(),
    Delete: () => {
      if (selectedItems.length > 0) setDeleteConfirmOpen(true);
    },
  });

  // R9-A item 2: virtualize the grid by row rather than by card, since
  // `useWindowVirtualizer` measures along a single axis and the grid wraps.
  // Column count and container width both track the grid container's own
  // ResizeObserver (mirroring the `grid-cols-*` breakpoints below) rather
  // than the viewport, so both stay correct regardless of any surrounding
  // chrome.
  //
  // Fix wave finding 1: this used to be a plain `useRef` + a `[]`-deps
  // `useEffect` that read `gridRef.current` -- on a normal page load the
  // FIRST render is always the `modelsQuery.isLoading` skeleton branch (see
  // below), which never mounts this grid `<div>` at all, so the effect ran
  // once against `null`, bailed, and never got another chance to attach once
  // the real grid mounted. A callback ref fixes it structurally: it fires
  // exactly when React actually mounts/unmounts the node, in whichever
  // branch that happens, and measures synchronously via
  // `getBoundingClientRect()` the moment it attaches instead of waiting for
  // the observer's first async callback.
  const gridNodeRef = useRef<HTMLDivElement | null>(null);
  const gridObserverRef = useRef<ResizeObserver | null>(null);
  const [columns, setColumns] = useState(() => columnsForWidth(0));
  const [containerWidth, setContainerWidth] = useState(0);

  const gridRef = useCallback((node: HTMLDivElement | null) => {
    gridObserverRef.current?.disconnect();
    gridObserverRef.current = null;
    gridNodeRef.current = node;
    if (!node) return;

    const rect = node.getBoundingClientRect();
    setColumns(columnsForWidth(rect.width));
    setContainerWidth(rect.width);

    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      setColumns(columnsForWidth(width));
      setContainerWidth(width);
    });
    observer.observe(node);
    gridObserverRef.current = observer;
  }, []);

  // List view is always one item per row -- `chunkIntoRows(items, 1)`
  // already degenerates to that, so the grid's own column count just isn't
  // consulted for row-chunking or the painted `gridTemplateColumns` in that
  // mode (see the render branch below).
  const effectiveColumns = viewMode === "list" ? 1 : columns;
  const rows = useMemo(() => chunkIntoRows(items, effectiveColumns), [items, effectiveColumns]);

  // Fix round 1: cards are aspect-square, so a row's height scales directly
  // with column width -- a single fixed guess (e.g. one number for both a
  // 2-column phone layout and a 5-column desktop one) overlaps or gaps rows
  // in production. `estimateRowHeight` derives it from the container's own
  // measured width instead; `measure()` below re-runs the virtualizer's
  // layout whenever that estimate changes (width/column changes). List rows
  // are a fixed height regardless of width (`estimateListRowHeight`).
  const rowHeightEstimate = useMemo(
    () => (viewMode === "list" ? estimateListRowHeight() : estimateRowHeight(containerWidth, columns)),
    [viewMode, containerWidth, columns],
  );
  const rowVirtualizer = useWindowVirtualizer({
    count: rows.length,
    estimateSize: () => rowHeightEstimate,
    overscan: 3,
    scrollMargin: gridNodeRef.current?.offsetTop ?? 0,
    // The default measures via ResizeObserver entries / getBoundingClientRect,
    // which is exactly right in a real browser -- but jsdom (tests) reports
    // 0 for every element's layout box, which would otherwise collapse every
    // row to zero height and defeat virtualization. Falling back to the
    // (now width-aware) estimate keeps behavior correct in both.
    measureElement: (element) => {
      const height = element.getBoundingClientRect().height;
      return height > 0 ? height : rowHeightEstimate;
    },
  });

  useEffect(() => {
    rowVirtualizer.measure();
    // Only re-measure when the estimate itself changes -- `rowVirtualizer`
    // is a new object identity every render and would otherwise re-run this
    // on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rowHeightEstimate]);

  const virtualRows = rowVirtualizer.getVirtualItems();

  useEffect(() => {
    const lastVisible = virtualRows.at(-1);
    if (!lastVisible) return;
    if (lastVisible.index >= rows.length - 1 && hasNextPage && !isFetchingNextPage) {
      void fetchNextPage();
    }
  }, [virtualRows, rows.length, hasNextPage, isFetchingNextPage, fetchNextPage]);

  const isEmpty = !modelsQuery.isLoading && items.length === 0;
  const tags = tagsQuery.data ?? [];

  const dragCounter = useRef(0);
  const [isDraggingFiles, setIsDraggingFiles] = useState(false);
  const createModel = useCreateModel();
  const queryClient = useQueryClient();

  const currentProject = useMemo(() => {
    return projects.find((p) => p.id === activeProject);
  }, [projects, activeProject]);

  useEffect(() => {
    function onWindowDragOver(e: DragEvent) {
      if (e.dataTransfer?.types?.includes("Files")) {
        e.preventDefault();
      }
    }
    function onWindowDrop(e: DragEvent) {
      if (e.dataTransfer?.types?.includes("Files")) {
        e.preventDefault();
      }
    }
    window.addEventListener("dragover", onWindowDragOver);
    window.addEventListener("drop", onWindowDrop);
    return () => {
      window.removeEventListener("dragover", onWindowDragOver);
      window.removeEventListener("drop", onWindowDrop);
    };
  }, []);

  async function uploadDroppedFiles(files: File[], targetProjectId: number | null) {
    await uploadFilesWithDuplicateHandling({
      files,
      targetProjectId,
      projects,
      createModel: (data) => createModel.mutateAsync(data),
      queryClient,
      onNavigate: (slug) => void navigate({ to: "/models/$slug", params: { slug } }),
    });
  }

  function handlePageDragEnter(e: React.DragEvent) {
    e.preventDefault();
    if (e.dataTransfer.types?.includes("Files")) {
      dragCounter.current += 1;
      setIsDraggingFiles(true);
    }
  }

  function handlePageDragOver(e: React.DragEvent) {
    e.preventDefault();
    if (e.dataTransfer.types?.includes("Files")) {
      e.dataTransfer.dropEffect = "copy";
    }
  }

  function handlePageDragLeave(e: React.DragEvent) {
    e.preventDefault();
    if (e.dataTransfer.types?.includes("Files")) {
      dragCounter.current -= 1;
      if (dragCounter.current <= 0) {
        dragCounter.current = 0;
        setIsDraggingFiles(false);
      }
    }
  }

  async function handlePageDrop(e: React.DragEvent) {
    e.preventDefault();
    dragCounter.current = 0;
    setIsDraggingFiles(false);

    // If the drop was already handled by a child element (folder card, model card merge),
    // the child called e.stopPropagation() which sets nativeEvent.cancelBubble = true.
    // React synthetic events still bubble, but we can detect this via the nativeEvent.
    if ((e.nativeEvent as Event & { cancelBubble?: boolean }).cancelBubble) return;

    // Ignore internal card-to-card drag (not OS files)
    if (e.dataTransfer.types.includes("application/json")) return;

    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      const files = Array.from(e.dataTransfer.files);
      await uploadDroppedFiles(files, activeProject ?? null);
    }
  }

  return (
    <div
      className="relative space-y-5 min-h-[calc(100vh-8rem)]"
      onDragEnter={handlePageDragEnter}
      onDragOver={handlePageDragOver}
      onDragLeave={handlePageDragLeave}
      onDrop={handlePageDrop}
    >
      {isDraggingFiles && (
        <div className="fixed inset-0 z-50 bg-background/80 backdrop-blur-xs flex flex-col items-center justify-center p-6 text-center animate-in fade-in pointer-events-none">
          <div className="flex flex-col items-center gap-4 max-w-lg p-10 rounded-2xl border-2 border-dashed border-primary bg-primary/10 shadow-2xl scale-105 transition-all">
            <div className="p-4 rounded-full bg-primary/20 text-primary">
              <UploadCloudIcon className="size-12 animate-bounce" />
            </div>
            <div className="space-y-1.5">
              <h3 className="text-xl font-bold text-foreground">
                {currentProject
                  ? `Déposer pour importer dans "${currentProject.name}"`
                  : "Déposer pour importer dans la bibliothèque"}
              </h3>
              <p className="text-sm text-muted-foreground">
                Fichiers .stl, .3mf, .obj, .step déposés n'importe où dans la page seront importés automatiquement.
              </p>
            </div>
          </div>
        </div>
      )}
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative max-w-sm flex-1">
            <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              ref={searchInputRef}
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="Search models…"
              className="pl-8"
              aria-label="Search models"
            />
          </div>
          <Select value={sort} onValueChange={setSort}>
            <SelectTrigger aria-label="Sort by">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SORT_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <div className="flex-1" />
          <Button
            type="button"
            variant={selectMode || selectedIds.size > 0 ? "secondary" : "outline"}
            className="gap-1.5"
            onClick={() => {
              if (selectMode || selectedIds.size > 0) {
                clearSelection();
              } else {
                setSelectMode(true);
              }
            }}
            title="Activer le mode sélection pour sélectionner, supprimer ou déplacer rapidement"
          >
            <CheckSquareIcon className={cn("size-4", (selectMode || selectedIds.size > 0) && "text-primary")} />
            <span>{selectMode || selectedIds.size > 0 ? "Annuler sélection" : "Sélectionner"}</span>
          </Button>
          <ViewModeToggle value={viewMode} onChange={handleViewModeChange} />
          <Button type="button" variant="outline" disabled={triggerScan.isPending} onClick={startScan}>
            <RefreshCwIcon className={triggerScan.isPending ? "animate-spin" : undefined} />
            Scan library
          </Button>
          <NewModelDialog
            trigger={
              <Button type="button">
                <PlusIcon /> New model
              </Button>
            }
          />
        </div>

        {/* Project Folders & Drag-and-drop management */}
        {viewMode !== "folders" && (
          <ProjectFolderView
            activeProjectId={activeProject}
            onSelectProject={goToProject}
            totalModelsInView={items.length}
          />
        )}

        {/* Manufacturing status facet */}
        {viewMode !== "folders" && (
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by manufacturing status">
            <FilterChip active={!activePrintStatus} onClick={() => goToPrintStatus(undefined)}>
              All statuses
            </FilterChip>
            {ALL_PRINT_STATUSES.map((st) => {
              const meta = getPrintStatusMeta(st);
              return (
                <FilterChip
                  key={st}
                  active={activePrintStatus === st}
                  onClick={() => goToPrintStatus(activePrintStatus === st ? undefined : st)}
                >
                  <span aria-hidden="true" className={cn("size-1.5 rounded-full", meta.dotClass)} />
                  {meta.label}
                </FilterChip>
              );
            })}
          </div>
        )}

        {categories.length > 0 && viewMode !== "folders" && (
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by category">
            <FilterChip active={!activeCategory} onClick={() => goToCategory(undefined)}>
              All categories
            </FilterChip>
            {categories.map((category) => (
              <FilterChip
                key={category.id}
                active={activeCategory === category.id}
                onClick={() => goToCategory(activeCategory === category.id ? undefined : category.id)}
              >
                <span
                  aria-hidden="true"
                  className={cn("size-1.5 rounded-full", tagColorClass(category.color) ?? "bg-muted-foreground")}
                />
                {category.name}
              </FilterChip>
            ))}
          </div>
        )}

        {viewMode !== "folders" && (
        <div className="flex flex-wrap items-center gap-2">
          {/* Format (File type) Popover */}
          <Popover>
            <PopoverTrigger asChild>
              <Button
                type="button"
                variant={activeFormat ? "default" : "outline"}
                size="sm"
                className="h-8 gap-1.5 text-xs"
                aria-label="Filter by format"
              >
                <FileIcon className="size-3.5" />
                <span>{activeFormat ? `Format: ${FORMAT_LABELS[activeFormat]}` : "Format"}</span>
                {activeFormat && (
                  <span
                    role="button"
                    tabIndex={0}
                    onClick={(e) => {
                      e.stopPropagation();
                      setActiveFormat(undefined);
                    }}
                    className="ml-0.5 rounded-full p-0.5 hover:bg-background/20"
                    aria-label="Clear format filter"
                  >
                    <XIcon className="size-3" />
                  </span>
                )}
              </Button>
            </PopoverTrigger>
            <PopoverContent align="start" className="w-64 p-3">
              <div className="space-y-2">
                <div className="flex items-center justify-between text-xs font-medium text-muted-foreground pb-1.5 border-b border-border/50">
                  <span>Type de fichier</span>
                  {activeFormat && (
                    <button
                      type="button"
                      onClick={() => setActiveFormat(undefined)}
                      className="text-[11px] text-primary hover:underline"
                    >
                      Effacer
                    </button>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by format">
                  <FilterChip active={!activeFormat} onClick={() => setActiveFormat(undefined)}>
                    All
                  </FilterChip>
                  {BLOB_FORMATS.map((format) => (
                    <FilterChip
                      key={format}
                      active={activeFormat === format}
                      onClick={() => setActiveFormat(activeFormat === format ? undefined : format)}
                    >
                      {FORMAT_LABELS[format]}
                    </FilterChip>
                  ))}
                </div>
              </div>
            </PopoverContent>
          </Popover>

          <Popover>
            <PopoverTrigger asChild>
              <Button type="button" variant={activeTag ? "default" : "outline"} size="sm" className="h-8 gap-1.5 text-xs">
                <TagIcon className="size-3.5" /> {activeTag ? `Tag: ${activeTag}` : "Tags"}
              </Button>
            </PopoverTrigger>
            <PopoverContent align="start" className="w-64">
              <div className="flex flex-wrap gap-1.5">
                {tags.map((tag) => (
                  <button
                    key={tag.id}
                    type="button"
                    onClick={() => setActiveTag(activeTag === tag.name ? undefined : tag.name)}
                  >
                    <Badge
                      variant={activeTag === tag.name ? "default" : "outline"}
                      className={cn(
                        "cursor-pointer",
                        activeTag !== tag.name && tagColorClass(tag.color),
                      )}
                    >
                      {tag.name}
                    </Badge>
                  </button>
                ))}
                {tags.length === 0 && <p className="text-xs text-muted-foreground">No tags yet</p>}
              </div>
            </PopoverContent>
          </Popover>

          <Popover>
            <PopoverTrigger asChild>
              <Button type="button" variant="outline" size="sm">
                <BookmarkIcon /> {activeCollectionTitle ? `Collection: ${activeCollectionTitle}` : "Collection"}
              </Button>
            </PopoverTrigger>
            <PopoverContent align="start" className="w-64">
              <div className="flex flex-wrap gap-1.5">
                {collections.map((collection) => (
                  <button
                    key={collection.id}
                    type="button"
                    onClick={() =>
                      setActiveCollection(activeCollection === collection.id ? undefined : collection.id)
                    }
                  >
                    <Badge
                      variant={activeCollection === collection.id ? "default" : "outline"}
                      className="cursor-pointer"
                    >
                      {`${collection.title} (${collection.site})`}
                    </Badge>
                  </button>
                ))}
                {collections.length === 0 && (
                  <p className="text-xs text-muted-foreground">No followed collections</p>
                )}
              </div>
            </PopoverContent>
          </Popover>

          <div className="hidden h-5 w-px bg-border sm:block" />

          <Label className="flex items-center gap-2 text-sm font-normal cursor-pointer">
            <Checkbox
              checked={slicedOnly}
              onCheckedChange={(checked) => setSlicedOnly(checked === true)}
            />
            Sliced only
          </Label>

          <FilterChip active={favoritesOnly} onClick={() => setFavoritesOnly((prev) => !prev)}>
            <StarIcon className={favoritesOnly ? "fill-current" : undefined} />
            Favorites
          </FilterChip>

          <FilterChip active={archivedOnly} onClick={() => setArchivedOnly((prev) => !prev)}>
            <ArchiveIcon />
            Include archived
          </FilterChip>
        </div>
        )}
      </div>

      {viewMode === "folders" ? (
        <FolderBrowser path={path} onNavigate={goToPath} />
      ) : modelsQuery.isLoading ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6">
          {Array.from({ length: 12 }).map((_, index) => (
            <Skeleton key={index} className="aspect-[3/4] w-full rounded-xl" />
          ))}
        </div>
      ) : modelsQuery.isError ? (
        <Card className="mx-auto mt-12 max-w-md">
          <CardHeader className="items-center text-center">
            <CardTitle>Couldn&apos;t load models</CardTitle>
            <CardDescription>
              {modelsQuery.error instanceof ApiError
                ? modelsQuery.error.detail
                : "Something went wrong loading the gallery."}
            </CardDescription>
          </CardHeader>
          <div className="flex justify-center pb-4">
            <Button type="button" onClick={() => void modelsQuery.refetch()}>
              Retry
            </Button>
          </div>
        </Card>
      ) : isEmpty ? (
        activeProject !== undefined ? null : (
          <Card className="mx-auto mt-8 max-w-md">
            <CardHeader className="items-center text-center">
              <CardTitle>{projects.length > 0 && !isSearching ? "Aucun modèle à la racine" : "No models yet"}</CardTitle>
              <CardDescription>
                {projects.length > 0 && !isSearching
                  ? "Tous vos modèles sont organisés dans les dossiers ci-dessus. Glissez-en ici pour les sortir d'un dossier, ou ajoutez-en de nouveaux."
                  : "Upload your first 3D model to get started."}
              </CardDescription>
            </CardHeader>
            <div className="flex justify-center pb-4">
              <Button asChild>
                <Link to="/add">Add a model</Link>
              </Button>
            </div>
          </Card>
        )
      ) : (
        <>
          <div ref={gridRef} className="relative w-full" style={{ height: rowVirtualizer.getTotalSize() }}>
            {virtualRows.map((virtualRow) => {
              const row = rows[virtualRow.index] ?? [];
              if (viewMode === "list") {
                const model = row[0];
                if (!model) return null;
                return (
                  <div
                    key={virtualRow.key}
                    data-index={virtualRow.index}
                    ref={rowVirtualizer.measureElement}
                    className="absolute top-0 left-0 w-full"
                    style={{ transform: `translateY(${virtualRow.start - rowVirtualizer.options.scrollMargin}px)` }}
                  >
                    <ModelRow
                      model={model}
                      index={virtualRow.index}
                      selected={selectedIds.has(model.id)}
                      selectedIds={selectedIds}
                      selectMode={selectMode || selectedIds.size > 0}
                      onSelectChange={toggleSelected}
                      onModifiedClick={handleModifiedClick}
                      onMergeModels={handleMergeModels}
                    />
                  </div>
                );
              }
              return (
                <div
                  key={virtualRow.key}
                  data-index={virtualRow.index}
                  ref={rowVirtualizer.measureElement}
                  className="absolute top-0 left-0 grid w-full gap-4 pb-4"
                  style={{
                    transform: `translateY(${virtualRow.start - rowVirtualizer.options.scrollMargin}px)`,
                    // Fix wave finding 2: this MUST be driven by the same
                    // `columns` state that `chunkIntoRows` used to build
                    // `row` below -- viewport-based Tailwind `grid-cols-*`
                    // classes measure the *viewport*, while `columns` (and
                    // `chunkIntoRows`) measure the grid *container*
                    // (viewport minus the sidebar/padding chrome), so the two
                    // disagreed at every width where that chrome mattered.
                    gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
                  }}
                >
                  {row.map((model, columnIndex) => (
                    <ModelCard
                      key={model.id}
                      model={model}
                      index={virtualRow.index * columns + columnIndex}
                      selected={selectedIds.has(model.id)}
                      selectedIds={selectedIds}
                      selectMode={selectMode || selectedIds.size > 0}
                      onSelectChange={toggleSelected}
                      onModifiedClick={handleModifiedClick}
                      onMergeModels={handleMergeModels}
                    />
                  ))}
                </div>
              );
            })}
          </div>
          {modelsQuery.isFetchingNextPage && (
            <p className="py-4 text-center text-sm text-muted-foreground">Loading more…</p>
          )}
        </>
      )}

      {selectedItems.length > 0 && (
        <SelectionActionBar
          selectedItems={selectedItems}
          totalAvailable={items.length}
          onSelectAll={selectAll}
          onDone={clearSelection}
          deleteConfirmOpen={deleteConfirmOpen}
          onDeleteConfirmOpenChange={setDeleteConfirmOpen}
        />
      )}

      {mergeConfirmState && (
        <ConfirmDialog
          open={Boolean(mergeConfirmState)}
          onOpenChange={(open) => {
            if (!open) setMergeConfirmState(null);
          }}
          title={`Fusionner dans "${mergeConfirmState.target.name}" ?`}
          description={`Tous les fichiers de ${mergeConfirmState.sources.map((s) => `"${s.name}"`).join(", ")} seront regroupés dans "${mergeConfirmState.target.name}". ${mergeConfirmState.sources.length === 1 ? "Le modèle source sera supprimé." : "Les modèles sources seront supprimés."}`}
          confirmLabel="Fusionner"
          onConfirm={confirmMerge}
        />
      )}
    </div>
  );
}

/** Floating bulk-action bar (fixed bottom-center) shown once at least one
 * model is checked in select mode. Tag add/remove and favorite go through
 * `useBulkUpdateModels` (`POST /models/bulk`); queueing has no bulk endpoint,
 * so it loops `useEnqueueModel` over the selection instead. Delete goes
 * through `useBulkDeleteModels` (`POST /models/bulk-delete`, Round 11 T1). */
function SelectionActionBar({
  selectedItems,
  totalAvailable,
  onSelectAll,
  onDone,
  deleteConfirmOpen,
  onDeleteConfirmOpenChange,
}: {
  selectedItems: ModelSummary[];
  totalAvailable?: number;
  onSelectAll?: () => void;
  onDone: () => void;
  deleteConfirmOpen: boolean;
  onDeleteConfirmOpenChange: (open: boolean) => void;
}) {
  const [tagToAdd, setTagToAdd] = useState("");
  const [addTagOpen, setAddTagOpen] = useState(false);
  const [removeTagOpen, setRemoveTagOpen] = useState(false);
  const [queueing, setQueueing] = useState(false);

  const bulkUpdate = useBulkUpdateModels();
  const bulkDelete = useBulkDeleteModels();
  // `silentError`: this loops one enqueue mutation per selected model and
  // toasts a single summary below -- the global per-mutation error toast
  // would otherwise fire once per failed model on top of it.
  const enqueueModel = useEnqueueModel({ silentError: true });

  const ids = selectedItems.map((model) => model.id);
  const slugs = selectedItems.map((model) => model.slug);
  const tagsOnSelection = Array.from(new Set(selectedItems.flatMap((model) => model.tags))).sort();

  // The actions are mutually exclusive while any of them is in flight --
  // most importantly Delete vs the enqueue loop: models can otherwise be
  // hard-deleted out from under their own still-landing queue POSTs. The
  // `isPending` flags drive the `disabled` props (they re-render); `claim()`
  // is what actually holds the line, since two Enter presses in the SAME
  // tick both read the pre-render `isPending: false` and would otherwise
  // both fire.
  const busy = bulkUpdate.isPending || bulkDelete.isPending || queueing;
  const inFlight = useRef(false);

  /** Takes the single action slot, or returns false if something holds it. */
  function claim(): boolean {
    if (busy || inFlight.current) return false;
    inFlight.current = true;
    return true;
  }

  function release() {
    inFlight.current = false;
  }

  function addTag() {
    const trimmed = tagToAdd.trim();
    if (!trimmed || !claim()) return;
    bulkUpdate.mutate(
      { ids, add_tags: [trimmed] },
      {
        onSettled: release,
        onSuccess: (result) => {
          toast.success(`Tagged ${result.updated} model${result.updated === 1 ? "" : "s"}`);
          setTagToAdd("");
          setAddTagOpen(false);
          onDone();
        },
      },
    );
  }

  function removeTag(name: string) {
    if (!claim()) return;
    bulkUpdate.mutate(
      { ids, remove_tags: [name] },
      {
        onSettled: release,
        onSuccess: (result) => {
          toast.success(`Untagged ${result.updated} model${result.updated === 1 ? "" : "s"}`);
          setRemoveTagOpen(false);
          onDone();
        },
      },
    );
  }

  function favoriteSelection() {
    if (!claim()) return;
    bulkUpdate.mutate(
      { ids, favorite: true },
      {
        onSettled: release,
        onSuccess: (result) => {
          toast.success(`Favorited ${result.updated} model${result.updated === 1 ? "" : "s"}`);
          // Non-destructive -- keep the selection live instead of exiting.
        },
      },
    );
  }

  async function addToQueue() {
    if (!claim()) return;
    setQueueing(true);
    try {
      const results = await Promise.allSettled(ids.map((id) => enqueueModel.mutateAsync(id)));
      const succeeded = results.filter((result) => result.status === "fulfilled").length;
      const failed = results.length - succeeded;
      if (succeeded > 0) toast.success(`Added ${succeeded} model${succeeded === 1 ? "" : "s"} to queue`);
      // Fix-review F3: `Promise.allSettled` swallows rejections silently --
      // without this, a total failure (e.g. every model already queued)
      // left the user with no feedback at all.
      if (failed > 0) toast.error(`Failed to add ${failed} model${failed === 1 ? "" : "s"} to queue`);
    } finally {
      setQueueing(false);
      release();
    }
  }

  const projectsQuery = useProjects();
  const projects = projectsQuery.data ?? EMPTY_PROJECTS;

  function assignProject(projectId: number | null) {
    if (!claim()) return;
    bulkUpdate.mutate(
      { ids, project_id: projectId ?? 0 },
      {
        onSettled: release,
        onSuccess: (result) => {
          toast.success(
            projectId
              ? `Assigned ${result.updated} model${result.updated === 1 ? "" : "s"} to project`
              : `Removed ${result.updated} model${result.updated === 1 ? "" : "s"} from project`,
          );
        },
      },
    );
  }

  function assignStatus(status: PrintStatus) {
    if (!claim()) return;
    bulkUpdate.mutate(
      { ids, print_status: status },
      {
        onSettled: release,
        onSuccess: (result) => {
          toast.success(`Updated status of ${result.updated} model${result.updated === 1 ? "" : "s"}`);
        },
      },
    );
  }

  function deleteSelection() {
    if (!claim()) return;
    // No local onError: queryClient.ts's global MutationCache.onError
    // already toasts the ApiError detail. The selection survives a failure
    // either way -- there's nothing to exit out of if the delete didn't
    // happen (or only partially happened; the hook's onSettled refetch
    // reconciles the gallery in that case).
    bulkDelete.mutate(
      { ids, slugs },
      {
        onSettled: release,
        onSuccess: (result) => {
          toast.success(`Deleted ${result.deleted} model${result.deleted === 1 ? "" : "s"}`);
          onDone();
        },
      },
    );
  }

  return (
    <Card className="fixed inset-x-0 bottom-6 z-40 mx-auto w-fit flex-row items-center gap-3 px-4 py-2.5 shadow-lg">
      <div className="flex items-center gap-2 pr-1 border-r border-border">
        <span className="text-sm font-semibold">
          {selectedItems.length} selected
        </span>
        {totalAvailable !== undefined && totalAvailable > 0 && onSelectAll && (
          <Button
            type="button"
            variant="ghost"
            size="xs"
            className="h-6 text-xs text-muted-foreground hover:text-foreground px-1.5"
            onClick={selectedItems.length >= totalAvailable ? onDone : onSelectAll}
          >
            {selectedItems.length >= totalAvailable ? "Désélectionner" : `Tout (${totalAvailable})`}
          </Button>
        )}
      </div>

      <Popover open={addTagOpen} onOpenChange={setAddTagOpen}>
        <PopoverTrigger asChild>
          <Button type="button" variant="outline" size="sm" disabled={busy}>
            <TagIcon /> Add tag
          </Button>
        </PopoverTrigger>
        <PopoverContent align="center" className="w-56">
          <Input
            autoFocus
            value={tagToAdd}
            placeholder="Tag name…"
            aria-label="Tag to add"
            onChange={(event) => setTagToAdd(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                addTag();
              }
            }}
          />
          <Button
            type="button"
            size="sm"
            className="mt-2 w-full"
            disabled={!tagToAdd.trim() || busy}
            onClick={addTag}
          >
            Add
          </Button>
        </PopoverContent>
      </Popover>

      <Popover open={removeTagOpen} onOpenChange={setRemoveTagOpen}>
        <PopoverTrigger asChild>
          <Button type="button" variant="outline" size="sm" disabled={busy}>
            <XIcon /> Remove tag
          </Button>
        </PopoverTrigger>
        <PopoverContent align="center" className="w-56">
          {tagsOnSelection.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {tagsOnSelection.map((name) => (
                <button key={name} type="button" disabled={busy} onClick={() => removeTag(name)}>
                  <Badge variant="outline" className="cursor-pointer">
                    {name}
                  </Badge>
                </button>
              ))}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">None of the selected models have tags.</p>
          )}
        </PopoverContent>
      </Popover>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button type="button" variant="outline" size="sm" disabled={busy} title="Déplacer vers un dossier">
            <FolderInputIcon className="size-3.5" /> Project
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="center" className="w-56">
          <DropdownMenuItem onClick={() => assignProject(null)}>
            <span className="flex items-center gap-2 text-muted-foreground">
              <XIcon className="size-3.5" /> None (Unassign / Racine)
            </span>
          </DropdownMenuItem>
          {projects.map((project) => {
            const ProjIcon = getProjectIcon(project.icon);
            return (
              <DropdownMenuItem key={project.id} onClick={() => assignProject(project.id)}>
                <span className="flex items-center gap-2">
                  <ProjIcon className="size-3.5 text-muted-foreground shrink-0" />
                  <span
                    aria-hidden="true"
                    className={cn("size-2 rounded-full shrink-0", tagColorClass(project.color) ?? "bg-muted-foreground")}
                  />
                  <span className="truncate">{project.name}</span>
                </span>
              </DropdownMenuItem>
            );
          })}
        </DropdownMenuContent>
      </DropdownMenu>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button type="button" variant="outline" size="sm" disabled={busy}>
            <CheckCircle2Icon className="size-3.5" /> Status
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="center" className="w-44">
          {ALL_PRINT_STATUSES.map((st) => {
            const meta = getPrintStatusMeta(st);
            return (
              <DropdownMenuItem key={st} onClick={() => assignStatus(st)}>
                <span className="flex items-center gap-2">
                  <span aria-hidden="true" className={cn("size-2 rounded-full", meta.dotClass)} />
                  {meta.label}
                </span>
              </DropdownMenuItem>
            );
          })}
        </DropdownMenuContent>
      </DropdownMenu>

      <Button type="button" variant="outline" size="sm" disabled={busy} onClick={favoriteSelection}>
        <StarIcon /> Favorite
      </Button>

      <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void addToQueue()}>
        <ListPlusIcon /> Add to queue
      </Button>

      <ConfirmDialog
        trigger={
          <Button type="button" variant="destructive" size="sm" disabled={busy}>
            <Trash2Icon /> Delete
          </Button>
        }
        open={deleteConfirmOpen}
        onOpenChange={onDeleteConfirmOpenChange}
        title={`Delete ${ids.length} model${ids.length === 1 ? "" : "s"}?`}
        description="Permanently deletes the selected models and every file they store. This cannot be undone."
        confirmLabel="Delete"
        destructive
        onConfirm={deleteSelection}
      />

      <Button type="button" variant="ghost" size="sm" onClick={onDone}>
        Cancel
      </Button>
    </Card>
  );
}
