/**
 * Per-file row actions as `OverflowItem`s (view/arrange/open/download/cover and
 * delete), shared by `FilesTab` and `DocsTab` rows via `FileEntry`'s single
 * overflow menu.
 *
 * `SendToPrinterButton` and `OpenInSlicerButton` are deliberately kept OUT of
 * the menu and rendered as sibling icon buttons by the caller: both already
 * own a full trigger/dialog, and nesting an interactive dialog trigger inside
 * a dropdown-menu item is fragile (the menu unmounting on close would tear
 * down the dialog with it).
 */
import { useState, type ReactNode } from "react";
import { BoxesIcon, DownloadIcon, EyeIcon, LayoutGridIcon, ImageIcon, StarIcon, Trash2Icon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { useDeleteFile, usePatchModel } from "@/api/library";
import { isOrcaSlicerOpenable, useOrcaSlicerEnabled } from "@/api/orcaslicer";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { OverflowItem } from "@/components/ui/overflow-menu";
import { useOrcaSlicerLauncher } from "@/components/orcaslicer/useOrcaSlicerLauncher";
import { canArrange } from "@/components/plate/arrangeable";
import { isStudioViewable } from "@/components/viewer/viewable";
import type { FileOut, ModelDetail } from "@/api/types";

export function useFileActionItems({
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
}): { fileItems: OverflowItem[]; dangerItems: OverflowItem[]; dialogs: ReactNode } {
  const deleteFile = useDeleteFile(model.slug);
  const patchModel = usePatchModel(model.slug);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const { t } = useTranslation();
  const orcaslicerEnabled = useOrcaSlicerEnabled();
  const orcaslicer = useOrcaSlicerLauncher(model.slug, orcaslicerEnabled);
  const showOrcaSlicer = orcaslicerEnabled && !isDoc && !!file.verified_at && isOrcaSlicerOpenable(file);

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
        onError: () => toast.error(t("mdFileActions.couldNotUpdateCover")),
      },
    );
  }

  const fileItems: OverflowItem[] = [
    {
      id: "view",
      label: t("mdFileActions.view"),
      icon: EyeIcon,
      hidden: !showViewIn3D,
      ariaLabel: t("mdFileActions.viewAria", { path: file.rel_path }),
      onSelect: () => onViewIn3D?.(file),
    },
    {
      id: "arrange",
      label: t("mdFileActions.arrange"),
      icon: LayoutGridIcon,
      hidden: isDoc || !onArrange || !canArrange(file),
      ariaLabel: t("mdFileActions.arrangeAria", { path: file.rel_path }),
      onSelect: () => onArrange?.(file),
    },
    {
      id: "orca",
      label: t("orcaslicer.open"),
      icon: BoxesIcon,
      hidden: !showOrcaSlicer,
      ariaLabel: t("mdFileActions.openOrcaAria", { path: file.rel_path }),
      onSelect: () => orcaslicer.launch(file),
    },
    {
      id: "download",
      label: t("mdFileActions.download"),
      icon: DownloadIcon,
      ariaLabel: t("mdFileActions.downloadAria", { path: file.rel_path }),
      ...(file.verified_at
        ? { href: `/api/files/${file.id}/download` }
        : { disabled: true, title: t("mdFileActions.processing") }),
    },
    {
      id: "cover",
      label: t("mdFileActions.setCover"),
      icon: ImageIcon,
      hidden: !canSetCover,
      onSelect: () => setAsCoverOrPreview(t("mdFileActions.coverUpdated")),
    },
    {
      id: "preview",
      label: t("mdFileActions.setPreview"),
      icon: StarIcon,
      hidden: !canSetPreview,
      onSelect: () => setAsCoverOrPreview(t("mdFileActions.previewUpdated")),
    },
  ];
  const dangerItems: OverflowItem[] = [
    {
      id: "delete",
      label: t("common.delete"),
      icon: Trash2Icon,
      destructive: true,
      ariaLabel: t("mdFileActions.deleteAria", { path: file.rel_path }),
      onSelect: (event) => {
        event.preventDefault();
        setConfirmDeleteOpen(true);
      },
    },
  ];
  const dialogs = (
    <>
      {orcaslicer.dialog}
      <ConfirmDialog
        open={confirmDeleteOpen}
        onOpenChange={setConfirmDeleteOpen}
        title={t("mdFileActions.deleteTitle", { path: file.rel_path })}
        description={t("mdFileActions.deleteDesc")}
        confirmLabel={t("common.delete")}
        destructive
        onConfirm={() => deleteFile.mutate(file.id)}
      />
    </>
  );
  return { fileItems, dangerItems, dialogs };
}
