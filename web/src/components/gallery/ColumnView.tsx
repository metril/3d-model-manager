import { ChevronRightIcon, FolderIcon } from "lucide-react";
import { useState } from "react";

import { useStorageTree } from "@/api/storageTree";
import type { StorageTreeFile } from "@/api/types";
import { FilePreview } from "@/components/gallery/FilePreview";
import { Skeleton } from "@/components/ui/skeleton";
import { humanizeBytes } from "@/lib/format";
import { cn } from "@/lib/utils";

function Column({
  path,
  selectedDir,
  selectedFile,
  onDir,
  onFile,
}: {
  path: string;
  selectedDir: string | null;
  selectedFile: number | null;
  onDir: (path: string) => void;
  onFile: (file: StorageTreeFile) => void;
}) {
  const tree = useStorageTree(path);
  const item = "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-muted";
  const dirs = tree.data?.dirs ?? [];
  const files = tree.data?.files ?? [];
  return (
    <div role="listbox" aria-label={path || "Library"} className="w-56 shrink-0 space-y-0.5 overflow-y-auto border-r p-1">
      {tree.isLoading ? (
        <Skeleton className="h-8 w-full" />
      ) : (
        <>
          {dirs.map((d) => (
            <button
              key={d.path}
              type="button"
              role="option"
              aria-selected={selectedDir === d.path}
              className={cn(item, selectedDir === d.path && "bg-muted font-medium")}
              onClick={() => onDir(d.path)}
            >
              <FolderIcon className="size-4 shrink-0 text-muted-foreground" />
              <span className="flex-1 truncate">{d.name}</span>
              <ChevronRightIcon className="size-3.5 text-muted-foreground" />
            </button>
          ))}
          {files.map((f) => (
            <button
              key={f.id}
              type="button"
              role="option"
              aria-selected={selectedFile === f.id}
              className={cn(item, selectedFile === f.id && "bg-muted font-medium")}
              onClick={() => onFile(f)}
            >
              <span className="flex-1 truncate">{f.name}</span>
              <span className="text-xs text-muted-foreground">{humanizeBytes(f.size)}</span>
            </button>
          ))}
          {dirs.length === 0 && files.length === 0 && <p className="px-2 py-1.5 text-sm text-muted-foreground">Empty</p>}
        </>
      )}
    </div>
  );
}

/** Miller-column browser: one column per path segment; a selected file opens a zoomable preview pane. */
export function ColumnView({ path, onNavigate }: { path: string; onNavigate: (path: string) => void }) {
  const segments = path.split("/").filter(Boolean);
  const columnPaths = ["", ...segments.map((_, i) => segments.slice(0, i + 1).join("/"))];
  const [file, setFile] = useState<StorageTreeFile | null>(null);
  const last = columnPaths.length - 1;
  return (
    <div className="flex min-h-72 overflow-x-auto rounded-lg border" data-testid="column-view">
      {columnPaths.map((p, i) => (
        <Column
          key={p || "root"}
          path={p}
          selectedDir={columnPaths[i + 1] ?? null}
          selectedFile={i === last ? (file?.id ?? null) : null}
          onDir={(d) => {
            setFile(null);
            onNavigate(d);
          }}
          onFile={setFile}
        />
      ))}
      {file ? (
        <div className="w-80 shrink-0 space-y-2 p-3">
          <p className="truncate text-sm font-medium" title={file.rel_path}>
            {file.name}
          </p>
          <p className="text-xs text-muted-foreground">{humanizeBytes(file.size)}</p>
          <FilePreview key={file.id} file={file} />
        </div>
      ) : null}
    </div>
  );
}
