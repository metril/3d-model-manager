import { memo, type DragEvent, type HTMLAttributes, type ReactNode } from "react";
import { FileTextIcon, FolderIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { EntityTags } from "@/components/tags/EntityTags";
import { CopyableHash } from "@/components/model-detail/CopyableHash";
import { FileActionsMenu } from "@/components/model-detail/FileActionsMenu";
import { FileRow } from "@/components/model-detail/FileRow";
import { FileThumb } from "@/components/model-detail/explorer/FileThumb";
import { fileMetaLine, isPreviewable } from "@/components/model-detail/explorer/fileMeta";
import { OrcaSlicerSplitButton } from "@/components/orcaslicer/OrcaSlicerSplitButton";
import { OpenInSlicerButton } from "@/components/model-detail/OpenInSlicerButton";
import { SendToPrinterButton } from "@/components/model-detail/SendToPrinterButton";
import { SliceFileButton } from "@/components/slice/SliceFileButton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { formatDate, formatDateTime, humanizeBytes } from "@/lib/format";
import { isSlicerEligible } from "@/lib/slicers";
import { cn } from "@/lib/utils";
import type { FileOut, ModelDetail } from "@/api/types";
import type { TreeNode } from "@/components/model-detail/explorer/tree";

export type EntryLayout = "list" | "grid";

export interface FileEntryProps {
  file: FileOut;
  model: ModelDetail;
  layout: EntryLayout;
  /** Name shown on the row (the base name inside a folder, the full path in search results). */
  label: string;
  /** Renders a selection checkbox when set. */
  selectable?: boolean;
  selected?: boolean;
  /** Callbacks take the file so the parent can pass one stable function to every row. */
  onSelectedChange?: (file: FileOut, on: boolean) => void;
  previewOpen?: boolean;
  onTogglePreview?: (file: FileOut) => void;
  onViewIn3D?: (file: FileOut) => void;
  onArrange?: (file: FileOut) => void;
  /** Extra per-file actions (PR 4: rename, move). Rendered before the actions menu. */
  actionsSlot?: ReactNode;
  /** Starts a drag-to-move of this file (PR 4); makes the row draggable when set. */
  onDragStartFile?: (event: DragEvent, file: FileOut) => void;
}

function FileFacts({ file, model, hash = true }: { file: FileOut; model: ModelDetail; hash?: boolean }) {
  const { t } = useTranslation();
  return (
    <>
      <span>{humanizeBytes(file.size)}</span>
      <span className="@sm/files:hidden">{formatDate(file.mtime)}</span>
      <span className="hidden @sm/files:inline">{formatDateTime(file.mtime)}</span>
      <Badge variant={file.verified_at ? "secondary" : "outline"}>
        {file.verified_at ? t("explorer.stored") : t("explorer.processing")}
      </Badge>
      {hash ? <CopyableHash hash={file.blob_hash} compact /> : null}
      <EntityTags
        target={{ kind: "file", modelId: model.id, relPath: file.rel_path }}
        tags={file.tag_refs}
        className="basis-full"
      />
    </>
  );
}

function FileActions({
  file,
  model,
  previewOpen,
  onTogglePreview,
  onViewIn3D,
  onArrange,
  actionsSlot,
}: Omit<FileEntryProps, "layout" | "label" | "selectable" | "selected" | "onSelectedChange" | "onDragStartFile">) {
  const { t } = useTranslation();
  const isDoc = file.kind === "doc";
  return (
    <>
      {onTogglePreview && isPreviewable(file) ? (
        <Button type="button" variant="outline" size="sm" aria-pressed={previewOpen} onClick={() => onTogglePreview(file)}>
          <FileTextIcon className="size-4" />
          <span className="sr-only @sm/files:not-sr-only">{previewOpen ? t("explorer.hidePreview") : t("explorer.preview")}</span>
        </Button>
      ) : null}
      {!isDoc ? (
        <>
          <SliceFileButton file={file} modelSlug={model.slug} />
          <SendToPrinterButton file={file} />
          <OrcaSlicerSplitButton file={file} slug={model.slug} />
          {file.verified_at && isSlicerEligible(file) ? <OpenInSlicerButton file={file} size="icon-sm" /> : null}
        </>
      ) : null}
      {actionsSlot}
      <FileActionsMenu file={file} model={model} isDoc={isDoc} onViewIn3D={onViewIn3D} onArrange={onArrange} />
    </>
  );
}

export const FileEntry = memo(function FileEntry(props: FileEntryProps) {
  const { file, model, layout, label, selectable, selected, onSelectedChange, onDragStartFile } = props;
  const { t } = useTranslation();
  const dragProps: HTMLAttributes<HTMLLIElement> = onDragStartFile
    ? { draggable: true, onDragStart: (event) => onDragStartFile(event, file) }
    : {};
  const checkbox =
    selectable && onSelectedChange ? (
      <Checkbox
        checked={selected === true}
        onCheckedChange={(v) => onSelectedChange(file, v === true)}
        aria-label={t("explorer.select", { path: file.rel_path })}
      />
    ) : null;
  const actions = <FileActions {...props} />;

  if (layout === "grid") {
    return (
      <li {...dragProps} className={cn("@container/files flex min-w-0 flex-col gap-2 rounded-lg border bg-card p-3", selected && "border-primary/60 bg-primary/5")}>
        <div className="flex items-start gap-2">
          {checkbox}
          <FileThumb file={file} className="size-16 shrink-0" />
          <div className="min-w-0 flex-1">
            <div className="truncate font-mono text-xs" title={file.rel_path}>
              {label}
            </div>
            {fileMetaLine(file) ? <div className="truncate text-[11px] text-muted-foreground">{fileMetaLine(file)}</div> : null}
          </div>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <FileFacts file={file} model={model} />
        </div>
        <div className="flex flex-wrap items-center gap-1">{actions}</div>
      </li>
    );
  }

  return (
    <FileRow
      liProps={dragProps}
      thumb={<FileThumb file={file} />}
      leading={checkbox}
      path={label}
      pathTitle={file.rel_path}
      meta={fileMetaLine(file)}
      facts={<FileFacts file={file} model={model} />}
      actions={actions}
    />
  );
});

/** A subfolder entry in the current folder. `actionsSlot` holds rename/move; it can be
 * selected, dragged, and dropped onto. */
export function FolderEntry({
  node,
  layout,
  onOpen,
  actionsSlot,
  selectable,
  selected,
  onSelectedChange,
  onDragStartFolder,
  dropProps,
}: {
  node: TreeNode;
  layout: EntryLayout;
  onOpen: (path: string) => void;
  actionsSlot?: ReactNode;
  selectable?: boolean;
  selected?: boolean;
  onSelectedChange?: (path: string, on: boolean) => void;
  onDragStartFolder?: (event: DragEvent, path: string) => void;
  dropProps?: (path: string) => HTMLAttributes<HTMLElement>;
}) {
  const { t } = useTranslation();
  const count = t("explorer.fileCount", { count: node.total });
  const checkbox =
    selectable && onSelectedChange ? (
      <Checkbox
        checked={selected === true}
        onCheckedChange={(v) => onSelectedChange(node.path, v === true)}
        aria-label={t("explorer.selectFolder", { path: node.path })}
      />
    ) : null;
  const liProps = {
    ...(dropProps?.(node.path) as HTMLAttributes<HTMLLIElement> | undefined),
    ...(onDragStartFolder ? { draggable: true, onDragStart: (event: DragEvent) => onDragStartFolder(event, node.path) } : {}),
  };
  if (layout === "grid") {
    return (
      <li
        {...liProps}
        className={cn(
          "flex items-center gap-2 rounded-lg border bg-card p-3 hover:bg-muted/50 data-[drop-over=true]:bg-primary/10 data-[drop-over=true]:ring-1 data-[drop-over=true]:ring-primary/50",
          selected && "border-primary/60 bg-primary/5",
        )}
      >
        {checkbox}
        <button type="button" className="flex min-w-0 flex-1 items-center gap-3 text-left" onClick={() => onOpen(node.path)}>
          <FolderIcon className="size-8 shrink-0 text-primary/70" />
          <span className="min-w-0">
            <span className="block truncate font-mono text-xs">{node.name}</span>
            <span className="block text-xs text-muted-foreground">{count}</span>
          </span>
        </button>
        {actionsSlot}
      </li>
    );
  }
  return (
    <FileRow
      liProps={liProps}
      leading={checkbox}
      thumb={
        <div className="flex size-10 items-center justify-center rounded bg-muted text-primary/70">
          <FolderIcon className="size-5" />
        </div>
      }
      path={`${node.name}/`}
      pathTitle={node.path}
      facts={<span>{count}</span>}
      actions={
        <>
          <Button type="button" variant="ghost" size="sm" onClick={() => onOpen(node.path)} aria-label={t("explorer.openFolder", { name: node.name })}>
            {t("explorer.open")}
          </Button>
          {actionsSlot}
        </>
      }
    />
  );
}
