import { useState, type ReactNode } from "react";
import { useNavigate } from "@tanstack/react-router";
import {
  ArchiveIcon,
  DownloadIcon,
  EllipsisIcon,
  FileArchiveIcon,
  FolderInputIcon,
  FolderKanbanIcon,
  ListPlusIcon,
  PencilIcon,
  StarIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react";
import { toast } from "sonner";

import { useAuth } from "@/api/auth";
import { useFeatures } from "@/api/features";
import { useArchiveModel, useDeleteModel, usePatchModel, useRedownloadModel } from "@/api/library";
import { useEnqueueModel } from "@/api/queue";
import { CategoryPicker } from "@/components/gallery/CategoryPicker";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { AddToProjectDialog } from "@/components/projects/AddToProjectDialog";
import { InlineEdit } from "@/components/InlineEdit";
import { useHotkeys } from "@/hooks/useHotkeys";
import { OpenInSlicerButton } from "@/components/model-detail/OpenInSlicerButton";
import { PrintDialog } from "@/components/print/PrintDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { formatDateTime } from "@/lib/format";
import { pickBestSlicerFile } from "@/lib/slicers";
import type { ModelDetail } from "@/api/types";

type RedownloadMode = "revision" | "replace";

/** "Re-download from source" (feat/import-fidelity T3, `POST
 * /models/{slug}/redownload`) -- an additive action, NOT gated behind edit
 * mode (same posture as "Add to queue"), reached via the header's "More
 * actions" overflow menu. Its menu item is disabled for a model with no
 * resolvable import source (`check_redownload_source` on the backend 409s
 * for the same reason) rather than opening a dialog that would just fail. */
function RedownloadDialog({
  model,
  open,
  onOpenChange,
}: {
  model: ModelDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [mode, setMode] = useState<RedownloadMode>("revision");
  const redownload = useRedownloadModel(model.slug);

  function handleStart() {
    redownload.mutate(
      { mode },
      {
        onSuccess: () => {
          onOpenChange(false);
          toast.success("Re-download started");
        },
      },
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) setMode("revision");
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Re-download from source</DialogTitle>
          <DialogDescription>
            Re-fetches this model&apos;s files fresh from where it was imported.
          </DialogDescription>
        </DialogHeader>
        <RadioGroup
          value={mode}
          onValueChange={(next) => setMode(next as RedownloadMode)}
          className="py-2"
        >
          <div className="flex items-center gap-2">
            <RadioGroupItem value="revision" id="redownload-mode-revision" />
            <Label htmlFor="redownload-mode-revision" className="font-normal">
              New revision (keeps current files as history)
            </Label>
          </div>
          <div className="flex items-center gap-2">
            <RadioGroupItem value="replace" id="redownload-mode-replace" />
            <Label htmlFor="redownload-mode-replace" className="font-normal">
              Replace current files
            </Label>
          </div>
        </RadioGroup>
        <DialogFooter>
          <Button type="button" disabled={redownload.isPending} onClick={handleStart}>
            {redownload.isPending ? "Starting..." : "Start"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ModelHeader({
  model,
  editMode,
  onToggleEditMode,
  onOpenRelocate,
  panelsMenu,
}: {
  model: ModelDetail;
  editMode: boolean;
  onToggleEditMode: () => void;
  onOpenRelocate: () => void;
  /** Dock "Panels" dropdown; only present when the dockable layout is active. */
  panelsMenu?: ReactNode;
}) {
  const navigate = useNavigate();
  const { data: me, can } = useAuth();
  const canDelete = can("admin") || (!!me && model.created_by_user_id === me.id);
  const patchModel = usePatchModel(model.slug);
  const archiveModel = useArchiveModel(model.slug);
  const deleteModel = useDeleteModel(model.slug);
  const enqueueModel = useEnqueueModel();

  const [redownloadOpen, setRedownloadOpen] = useState(false);
  const [projectOpen, setProjectOpen] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [printOpen, setPrintOpen] = useState(false);
  const canRedownload = Boolean(model.source_site && model.source_url);
  const needsReview = model.review_state === "adopted";
  // P5 Task 11: the header's "Print…" opens the shared `PrintDialog` --
  // only when there's a sliced file to plan against AND the viewer holds
  // the blanket `printers:print` permission (the dialog's own Send step
  // re-checks per-printer ACLs once a printer is picked).
  const features = useFeatures();
  const revFiles = model.current_revision?.files ?? [];
  const hasSlicedFile = revFiles.some((f) => f.format === "gcode_3mf");
  // Unsliced sources can be printed too when the slicer sidecar can slice them.
  const hasSliceableFile =
    !!features.data?.slicer_configured && revFiles.some((f) => ["3mf", "stl", "step", "obj"].includes(f.format));
  const canPrint = (hasSlicedFile || hasSliceableFile) && can("printers:print");

  // R9-C item 5: same mutation as the star button below.
  useHotkeys({ f: () => patchModel.mutate({ favorite: !model.favorite }) });

  // R10-C: the header's "Open in slicer" split button targets the best
  // slicer-eligible (3mf/step/obj/stl/iges) stored file on the current
  // revision, per `SLICER_FORMAT_PRIORITY` -- there's no broader "primary
  // file" concept to hang this off of yet.
  const slicerFile = pickBestSlicerFile(model.current_revision?.files ?? []);

  return (
    <div className="space-y-3 border-b border-border pb-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1 space-y-2">
          {/* Keep the page's h1 in the heading outline even while the name
              is editable -- InlineEdit renders spans, so nesting it here is
              valid and screen readers still see a level-1 heading in both
              modes. */}
          <h1 className="text-2xl font-semibold">
            {editMode ? (
              <InlineEdit
                value={model.name}
                aria-label="name"
                onSave={(name) => {
                  if (name) patchModel.mutate({ name });
                }}
                className="text-2xl font-semibold"
              />
            ) : (
              model.name
            )}
          </h1>
          <div className="flex flex-wrap items-center gap-2">
            {editMode ? (
              <CategoryPicker
                value={model.category_id ?? null}
                onChange={(categoryId) => patchModel.mutate({ category_id: categoryId })}
              />
            ) : model.category ? (
              <Badge variant="outline" className="gap-1">
                <span
                  aria-hidden="true"
                  className="size-1.5 rounded-full"
                  style={{ backgroundColor: model.category.color ?? undefined }}
                />
                {model.category.name}
              </Badge>
            ) : null}
            {model.is_archived ? <Badge variant="outline">Archived</Badge> : null}
            {needsReview ? (
              <Badge variant="secondary" className="gap-1 pr-1" data-testid="review-badge">
                Needs review
                <ConfirmDialog
                  trigger={
                    <button type="button" aria-label="Dismiss needs review" className="rounded-full hover:opacity-70">
                      <XIcon className="size-3" />
                    </button>
                  }
                  title='Clear "needs review"?'
                  confirmLabel="Clear"
                  onConfirm={() => patchModel.mutate({ review_state: null })}
                />
              </Badge>
            ) : null}
            {/* Not edit-gated -- a print history fact, not editable metadata,
                same treatment as the favorite star. */}
            {model.print_count > 0 ? (
              <Badge variant="secondary" title={`Last printed ${formatDateTime(model.last_printed_at)}`}>
                Printed {model.print_count}×
              </Badge>
            ) : null}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {/* A star is a deliberate, always-live action -- not part of the
              edit gate the way name/description/tags/archive are. */}
          <Button
            type="button"
            variant="outline"
            size="icon"
            aria-label={model.favorite ? "Remove from favorites" : "Add to favorites"}
            aria-pressed={model.favorite}
            onClick={() => patchModel.mutate({ favorite: !model.favorite })}
          >
            <StarIcon className={model.favorite ? "fill-amber-400 text-amber-500" : ""} />
          </Button>
          {slicerFile ? <OpenInSlicerButton file={slicerFile} size="default" /> : null}
          {canPrint ? (
            <>
              <Button type="button" variant="outline" onClick={() => setPrintOpen(true)}>
                Print…
              </Button>
              <PrintDialog open={printOpen} onOpenChange={setPrintOpen} source={{ kind: "model", modelId: model.id }} />
            </>
          ) : null}
          {/* Queueing a model to print is a normal action too, not an edit. */}
          <Button
            type="button"
            variant="outline"
            disabled={enqueueModel.isPending}
            onClick={() =>
              enqueueModel.mutate(model.id, {
                onSuccess: () => toast.success("Added to queue"),
              })
            }
          >
            <ListPlusIcon />
            Add to queue
          </Button>
          {panelsMenu}
          <Button type="button" variant="outline" onClick={onToggleEditMode}>
            <PencilIcon />
            {editMode ? "Done" : "Edit"}
          </Button>
          {/* Lifecycle actions (re-download, relocate, archive, delete) live
              behind this overflow menu regardless of edit mode -- edit mode
              only gates metadata (name/description/tags). Each item opens
              its existing dialog via controlled `open` state rather than a
              nested `DialogTrigger`, so the dialog survives the menu
              unmounting when it closes (standard Radix menu+dialog
              composition). */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button type="button" variant="outline" size="icon" aria-label="More actions">
                <EllipsisIcon />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem disabled={!canRedownload} onSelect={() => setRedownloadOpen(true)}>
                <DownloadIcon />
                Re-download…
              </DropdownMenuItem>
              {/* Plain anchor with `download` -- the session cookie carries
                  auth, so no fetch-and-blob dance is needed (R11-A). */}
              <DropdownMenuItem asChild>
                <a href={`/api/models/${model.slug}/zip`} download>
                  <FileArchiveIcon />
                  Download ZIP
                </a>
              </DropdownMenuItem>
              {can("library:write") ? (
                <DropdownMenuItem onSelect={() => setProjectOpen(true)}>
                  <FolderKanbanIcon />
                  Add to project…
                </DropdownMenuItem>
              ) : null}
              <DropdownMenuItem onSelect={onOpenRelocate}>
                <FolderInputIcon />
                Move / copy…
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => setArchiveOpen(true)}>
                <ArchiveIcon />
                Archive…
              </DropdownMenuItem>
              {canDelete ? (
                <DropdownMenuItem variant="destructive" onSelect={() => setDeleteOpen(true)}>
                  <Trash2Icon />
                  Delete…
                </DropdownMenuItem>
              ) : null}
            </DropdownMenuContent>
          </DropdownMenu>
          <AddToProjectDialog open={projectOpen} onOpenChange={setProjectOpen} modelIds={[model.id]} />
          <RedownloadDialog model={model} open={redownloadOpen} onOpenChange={setRedownloadOpen} />
          <ConfirmDialog
            open={archiveOpen}
            onOpenChange={setArchiveOpen}
            title={`Archive "${model.name}"?`}
            description="Archived models are hidden from the library by default. This does not delete files."
            confirmLabel="Archive"
            destructive
            onConfirm={() => archiveModel.mutate(true)}
          />
          <ConfirmDialog
            open={deleteOpen}
            onOpenChange={setDeleteOpen}
            title="Delete this model?"
            description="Permanently deletes the model and every file from storage. This cannot be undone."
            confirmLabel="Delete"
            destructive
            onConfirm={() =>
              deleteModel.mutate(undefined, {
                onSuccess: () => void navigate({ to: "/library" }),
              })
            }
          />
        </div>
      </div>
    </div>
  );
}
