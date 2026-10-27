import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { ChevronRightIcon, DownloadIcon, FolderIcon, SearchIcon } from "lucide-react";

import { TagChip } from "@/components/tags/TagChip";
import { tagRefsOf } from "@/components/tags/tagRefs";
import { useStorageTree } from "@/api/storageTree";
import { ApiError } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { humanizeBytes } from "@/lib/format";
import { formatIcon } from "@/lib/formatMeta";
import { tagColorClass } from "@/lib/tagColors";
import { cn } from "@/lib/utils";
import type { ModelSummary, StorageTreeFile } from "@/api/types";

/** Splits a `path` search param (`"figures/dnd/goblins"`) into its named
 * segments for the breadcrumb -- an empty/undefined path is the root, with
 * no segments at all. */
function pathSegments(path: string): string[] {
  return path.split("/").filter((segment) => segment.length > 0);
}

function joinPath(segments: string[]): string {
  return segments.join("/");
}

function FileRow({ file }: { file: StorageTreeFile }) {
  const Icon = formatIcon(file.format);
  return (
    <div className="flex items-center gap-3 rounded-lg border border-transparent px-2 py-2 hover:border-border hover:bg-muted/50">
      <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
        <Icon className="size-4" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium" title={file.rel_path}>
          {file.name}
        </p>
        <p className="truncate text-xs text-muted-foreground">{humanizeBytes(file.size)}</p>
      </div>
      <Link
        to="/models/$slug"
        params={{ slug: file.model_slug }}
        className="shrink-0 text-xs text-muted-foreground hover:text-foreground hover:underline"
      >
        Open model
      </Link>
      <Button asChild variant="ghost" size="icon-sm" aria-label={`Download ${file.name}`}>
        <a href={`/api/files/${file.id}/download`}>
          <DownloadIcon className="size-4" />
        </a>
      </Button>
    </div>
  );
}

/** Header strip shown above the file list when `GET /storage/tree`'s
 * `model` is non-null -- i.e. the current path is a single model's own
 * directory, not just an arbitrary folder. */
function ModelHeaderStrip({ model }: { model: ModelSummary }) {
  return (
    <Card className="flex-row items-center justify-between gap-3 p-4">
      <div className="min-w-0 space-y-1.5">
        <h2 className="truncate text-sm font-semibold">{model.name}</h2>
        <div className="flex flex-wrap items-center gap-1.5">
          {model.category && (
            <Badge variant="outline" className="gap-1">
              <span
                aria-hidden="true"
                className={cn("size-1.5 rounded-full", tagColorClass(model.category.color) ?? "bg-muted-foreground")}
              />
              {model.category.name}
            </Badge>
          )}
          {tagRefsOf(model).map((tag) => (
            <TagChip key={tag.name} tag={tag} />
          ))}
        </div>
      </div>
      <Button asChild variant="outline" size="sm" className="shrink-0">
        <Link to="/models/$slug" params={{ slug: model.slug }}>
          Open model
        </Link>
      </Button>
    </Card>
  );
}

/** Raw-storage folder navigation (R13b): one level of `GET /storage/tree` at
 * a time -- subfolders as cards, files directly in this folder as rows. A
 * plain file browser, not a picker: no selection/bulk actions here (those
 * live on the grid/list views). The current path is owned by the caller
 * (`LibraryPage`'s `?path=` search param) so switching view modes or
 * navigating away/back preserves it. */
export function FolderBrowser({ path, onNavigate }: { path: string; onNavigate: (path: string) => void }) {
  const [filter, setFilter] = useState("");
  const treeQuery = useStorageTree(path);
  const segments = pathSegments(path);

  const dirs = treeQuery.data?.dirs ?? [];
  const files = treeQuery.data?.files ?? [];
  const model = treeQuery.data?.model ?? null;

  // No `useMemo` here: `dirs`/`files` are freshly derived (`?? []`) every
  // render anyway, so memoizing against them would never hit, and the
  // filtering itself is cheap (a folder's own dir/file list, not the whole
  // library).
  const normalizedFilter = filter.trim().toLowerCase();
  const filteredDirs = dirs.filter((dir) => dir.name.toLowerCase().includes(normalizedFilter));
  const filteredFiles = files.filter((file) => file.name.toLowerCase().includes(normalizedFilter));

  function goToRoot() {
    onNavigate("");
  }

  function goToSegment(index: number) {
    onNavigate(joinPath(segments.slice(0, index + 1)));
  }

  function openDir(dirPath: string) {
    onNavigate(dirPath);
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <nav aria-label="Folder path" className="flex flex-wrap items-center gap-1 text-sm">
          <button
            type="button"
            onClick={goToRoot}
            className="rounded px-1.5 py-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            Library
          </button>
          {segments.map((segment, index) => (
            <span key={index} className="flex items-center gap-1">
              <ChevronRightIcon className="size-3.5 text-muted-foreground" />
              <button
                type="button"
                onClick={() => goToSegment(index)}
                className="rounded px-1.5 py-0.5 text-muted-foreground hover:bg-muted hover:text-foreground aria-current:text-foreground aria-current:font-medium"
                aria-current={index === segments.length - 1 ? "page" : undefined}
              >
                {segment}
              </button>
            </span>
          ))}
        </nav>
        <div className="relative max-w-xs flex-1">
          <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Filter this folder…"
            className="pl-8"
            aria-label="Filter this folder"
          />
        </div>
      </div>

      {treeQuery.isLoading ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, index) => (
            <Skeleton key={index} className="h-24 w-full rounded-xl" />
          ))}
        </div>
      ) : treeQuery.isError ? (
        <Card className="mx-auto mt-12 max-w-md">
          <CardHeader className="items-center text-center">
            <CardTitle>Couldn&apos;t load this folder</CardTitle>
            <CardDescription>
              {treeQuery.error instanceof ApiError ? treeQuery.error.detail : "Something went wrong."}
            </CardDescription>
          </CardHeader>
          <div className="flex justify-center pb-4">
            <Button type="button" onClick={() => void treeQuery.refetch()}>
              Retry
            </Button>
          </div>
        </Card>
      ) : filteredDirs.length === 0 && filteredFiles.length === 0 && !model ? (
        <Card className="mx-auto mt-12 max-w-md">
          <CardHeader className="items-center text-center">
            <CardTitle>{filter.trim() ? "No matches" : "This folder is empty"}</CardTitle>
            <CardDescription>
              {filter.trim() ? "Try a different filter." : "No subfolders or files here."}
            </CardDescription>
          </CardHeader>
        </Card>
      ) : (
        <>
          {model && <ModelHeaderStrip model={model} />}
          {filteredDirs.length > 0 && (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
              {filteredDirs.map((dir) => (
                <button
                  key={dir.path}
                  type="button"
                  onClick={() => openDir(dir.path)}
                  className="flex items-center gap-2 rounded-xl border border-border bg-card p-3 text-left hover:bg-muted"
                >
                  <FolderIcon className="size-8 shrink-0 text-muted-foreground" />
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium">{dir.name}</span>
                    <span className="block text-xs text-muted-foreground">
                      {dir.file_count} {dir.file_count === 1 ? "file" : "files"}, {dir.model_count}{" "}
                      {dir.model_count === 1 ? "model" : "models"}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          )}
          {filteredFiles.length > 0 && (
            <div className="space-y-0.5">
              {filteredFiles.map((file) => (
                <FileRow key={file.id} file={file} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
