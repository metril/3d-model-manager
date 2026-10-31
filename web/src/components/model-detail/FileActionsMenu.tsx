/**
 * Shared per-file row-actions menu (R13c), used by both `FilesTab` and
 * `DocsTab` so the download/delete/set-as-cover/set-as-preview/view-in-3D
 * logic lives in one place instead of being duplicated per tab.
 *
 * `SendToPrinterButton` and `OpenInSlicerButton` are deliberately kept OUT
 * of this menu and rendered as sibling icon buttons by the caller (see
 * `FilesTab.tsx`): both already own a full trigger/dialog (or split-button
 * dropdown) of their own, and nesting an interactive dialog trigger inside
 * a dropdown-menu item is fragile (the menu unmounting on close would tear
 * down the dialog with it) for no real benefit here.
 */
import { useState } from "react";
import { BoxesIcon, DownloadIcon, EyeIcon, LayoutGridIcon, ImageIcon, MoreVerticalIcon, StarIcon, Trash2Icon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { useDeleteFile, usePatchModel } from "@/api/library";
import { isStudioOpenable, useStudioEnabled } from "@/api/studio";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useStudioLauncher } from "@/components/studio/useStudioLauncher";
import { canArrange } from "@/components/plate/arrangeable";
import { isStudioViewable } from "@/components/viewer/viewable";
import type { FileOut, ModelDetail } from "@/api/types";

export function FileActionsMenu({
  file,
  model,
  onViewIn3D,
  onArrange,
  isDoc = false,
}: {
  file: FileOut;
  model: ModelDetail;
  /** R13c "View in 3D" hand-off -- see `FilesTab.tsx`. Never applicable for
   * doc files (`isDoc`), which are never studio-viewable. */
  onViewIn3D?: (file: FileOut) => void;
  /** Opens the plate editor on this single file (STL/OBJ/3MF with a 3D preview). */
  onArrange?: (file: FileOut) => void;
  /** Docs never offer "View in 3D" -- their own row already has an inline
   * preview toggle (`DocsTab.tsx`) instead of a menu-driven one. */
  isDoc?: boolean;
}) {
  const deleteFile = useDeleteFile(model.slug);
  const patchModel = usePatchModel(model.slug);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const { t } = useTranslation();
  const studioEnabled = useStudioEnabled();
  const studio = useStudioLauncher(model.slug, studioEnabled);
  const showStudio = studioEnabled && !isDoc && !!file.verified_at && isStudioOpenable(file);

  const showViewIn3D = !isDoc && !!onViewIn3D && isStudioViewable(file);
  const canSetCover = file.kind === "image";
  // R13c: there's no dedicated "gallery preview thumbnail" field server-side
  // -- `_gallery_cover_url` (backend/app/services/library.py) falls back to
  // a model's derived mesh thumbnail only once `cover_blob_hash` is unset,
  // so "Set as preview" reuses that SAME `cover_blob_hash` PATCH rather than
  // a separate concept that doesn't exist on the backend.
  const canSetPreview = file.glb_status === "ok";

  function setAsCoverOrPreview(successMessage: string) {
    patchModel.mutate(
      { cover_blob_hash: file.blob_hash },
      {
        onSuccess: () => toast.success(successMessage),
        onError: () => toast.error("Could not update cover"),
      },
    );
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button type="button" variant="ghost" size="icon-sm" aria-label={`Actions for ${file.rel_path}`}>
            <MoreVerticalIcon className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {showViewIn3D ? (
            <DropdownMenuItem aria-label={`View ${file.rel_path} in 3D`} onSelect={() => onViewIn3D?.(file)}>
              <EyeIcon className="size-4" />
              View in 3D
            </DropdownMenuItem>
          ) : null}
          {!isDoc && onArrange && canArrange(file) ? (
            <DropdownMenuItem aria-label={`Arrange ${file.rel_path} on plate`} onSelect={() => onArrange(file)}>
              <LayoutGridIcon className="size-4" />
              Arrange on plate…
            </DropdownMenuItem>
          ) : null}
          {showStudio ? (
            <DropdownMenuItem aria-label={`Open ${file.rel_path} in OrcaSlicer`} onSelect={() => studio.launch(file)}>
              <BoxesIcon className="size-4" />
              {t("studio.open")}
            </DropdownMenuItem>
          ) : null}
          {file.verified_at ? (
            <DropdownMenuItem asChild aria-label={`Download ${file.rel_path}`}>
              <a href={`/api/files/${file.id}/download`}>
                <DownloadIcon className="size-4" />
                Download
              </a>
            </DropdownMenuItem>
          ) : (
            <DropdownMenuItem
              disabled
              aria-label={`Download ${file.rel_path}`}
              title="Still processing — download will be available once verified"
            >
              <DownloadIcon className="size-4" />
              Download
            </DropdownMenuItem>
          )}
          {canSetCover || canSetPreview ? <DropdownMenuSeparator /> : null}
          {canSetCover ? (
            <DropdownMenuItem onSelect={() => setAsCoverOrPreview("Cover updated")}>
              <ImageIcon className="size-4" />
              Set as cover
            </DropdownMenuItem>
          ) : null}
          {canSetPreview ? (
            <DropdownMenuItem onSelect={() => setAsCoverOrPreview("Preview updated")}>
              <StarIcon className="size-4" />
              Set as preview
            </DropdownMenuItem>
          ) : null}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            variant="destructive"
            aria-label={`Delete ${file.rel_path}`}
            onSelect={(event) => {
              event.preventDefault();
              setConfirmDeleteOpen(true);
            }}
          >
            <Trash2Icon className="size-4" />
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {studio.dialog}
      <ConfirmDialog
        open={confirmDeleteOpen}
        onOpenChange={setConfirmDeleteOpen}
        title={`Delete ${file.rel_path}?`}
        description="This removes the file from the current revision."
        confirmLabel="Delete"
        destructive
        onConfirm={() => deleteFile.mutate(file.id)}
      />
    </>
  );
}
