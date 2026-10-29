/**
 * Tag Manager (Settings -> General): every tag with its usage counts, plus
 * rename / recolor / icon / description editing, merge-into, and delete.
 * Destructive actions confirm with the affected-row count (no type-to-confirm).
 */
import { useState } from "react";

import { ApiError } from "@/api/client";
import { useDeleteTag, useMergeTag, useTags, useUpdateTag } from "@/api/tags";
import type { TagOut, TagRef } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { TagChip } from "@/components/tags/TagChip";
import { TagForm } from "@/components/tags/TagForm";
import { TagPicker } from "@/components/tags/TagPicker";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useDiscardGuard } from "@/components/ui/use-discard-guard";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

function usage(tag: TagOut): string {
  const c = tag.counts;
  if (!c || c.total === 0) return "Unused";
  const parts: [number, string][] = [
    [c.models, "model"],
    [c.model_files, "file"],
    [c.projects, "project"],
    [c.attachments, "attachment"],
    [c.archives, "print"],
    [c.collections, "collection"],
  ];
  return parts
    .filter(([n]) => n > 0)
    .map(([n, label]) => `${n} ${label}${n === 1 ? "" : "s"}`)
    .join(", ");
}

function rows(n: number): string {
  return `${n} tagged item${n === 1 ? "" : "s"}`;
}

function TagRow({ tag, all }: { tag: TagOut; all: TagOut[] }) {
  const [editOpen, setEditOpen] = useState(false);
  const [mergeInto, setMergeInto] = useState<TagRef | null>(null);
  const updateTag = useUpdateTag();
  const deleteTag = useDeleteTag();
  const mergeTag = useMergeTag();
  const total = tag.counts?.total ?? 0;
  const [editDirty, setEditDirty] = useState(false);
  const guard = useDiscardGuard({ onOpenChange: setEditOpen, dirty: editDirty, busy: updateTag.isPending });

  return (
    <TableRow>
      <TableCell>
        <TagChip tag={tag} />
      </TableCell>
      <TableCell className="whitespace-normal text-muted-foreground">{tag.description || "—"}</TableCell>
      <TableCell className="whitespace-normal text-muted-foreground">{usage(tag)}</TableCell>
      <TableCell className="whitespace-normal">
        <div className="flex flex-wrap items-center gap-1.5">
          <Button type="button" size="sm" variant="outline" onClick={() => setEditOpen(true)}>
            Edit
          </Button>
          <TagPicker
            label="Merge into"
            onPick={setMergeInto}
            options={all.filter((other) => other.id !== tag.id)}
            trigger={
              <Button type="button" size="sm" variant="outline" disabled={all.length < 2}>
                Merge into…
              </Button>
            }
          />
          <ConfirmDialog
            trigger={
              <Button type="button" size="sm" variant="destructive" disabled={deleteTag.isPending}>
                Delete
              </Button>
            }
            title={`Delete "${tag.name}"?`}
            description={
              total > 0
                ? `It will be removed from ${rows(total)}. This can't be undone.`
                : "This tag isn't used anywhere."
            }
            confirmLabel="Delete"
            destructive
            onConfirm={() => deleteTag.mutate(tag.id)}
          />
        </div>

        <Dialog open={editOpen} onOpenChange={guard.requestChange}>
          <DialogContent dismissable={false}>
            <DialogHeader>
              <DialogTitle>Edit tag</DialogTitle>
            </DialogHeader>
            <TagForm
              initial={tag}
              submitLabel="Save changes"
              pending={updateTag.isPending}
              onCancel={() => guard.requestChange(false)}
              onDirtyChange={setEditDirty}
              onSubmit={(values) => updateTag.mutate({ id: tag.id, ...values }, { onSuccess: () => setEditOpen(false) })}
            />
            {updateTag.isError && (
              <p role="alert" className="text-sm text-destructive">
                {updateTag.error instanceof ApiError ? updateTag.error.detail : "Could not save this tag"}
              </p>
            )}
          </DialogContent>
        </Dialog>
        {guard.confirm}

        <ConfirmDialog
          open={mergeInto !== null}
          onOpenChange={(open) => {
            if (!open) setMergeInto(null);
          }}
          title={`Merge "${tag.name}" into "${mergeInto?.name ?? ""}"?`}
          description={
            total > 0
              ? `${rows(total)} will be re-tagged "${mergeInto?.name ?? ""}", then "${tag.name}" is deleted.`
              : `"${tag.name}" isn't used anywhere; it will just be deleted.`
          }
          confirmLabel="Merge"
          destructive
          onConfirm={() => {
            if (mergeInto) mergeTag.mutate({ id: tag.id, intoId: mergeInto.id });
          }}
        />
      </TableCell>
    </TableRow>
  );
}

export function TagManagerCard() {
  const tagsQuery = useTags();
  const tags = tagsQuery.data ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Tags</CardTitle>
        <CardDescription>
          Rename, recolor, merge or delete tags. Tags are created and applied from the tag pickers on models, files,
          projects, prints and collections.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {tagsQuery.isLoading ? (
          <Skeleton className="h-24 w-full rounded-lg" />
        ) : tagsQuery.isError ? (
          <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed py-8 text-center">
            <p role="alert" className="text-sm text-muted-foreground">
              {tagsQuery.error instanceof ApiError ? tagsQuery.error.detail : "Couldn't load tags."}
            </p>
            <Button type="button" size="sm" onClick={() => void tagsQuery.refetch()}>
              Retry
            </Button>
          </div>
        ) : tags.length === 0 ? (
          <p className="text-sm text-muted-foreground">No tags yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Tag</TableHead>
                <TableHead>Description</TableHead>
                <TableHead>Used on</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {tags.map((tag) => (
                <TagRow key={tag.id} tag={tag} all={tags} />
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
