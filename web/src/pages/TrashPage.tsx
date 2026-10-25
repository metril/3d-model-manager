/**
 * Library trash: models moved here by Delete stay restorable until purged
 * (manually, or automatically after the retention window in Settings).
 * Purges are count-confirm gated; the count only includes items the caller
 * may actually purge (owner or admin), so it never over-promises.
 */
import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { RotateCcwIcon, Trash2Icon } from "lucide-react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useAuth } from "@/api/auth";
import { usePurgeTrash, useRestoreTrash, useTrash } from "@/api/trash";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { PageContainer } from "@/components/ui/page-container";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateTime, humanizeBytes } from "@/lib/format";

const plural = (n: number) => `${n} model${n === 1 ? "" : "s"}`;

export function TrashPage() {
  const query = useTrash();
  const { data: me, can } = useAuth();
  const restore = useRestoreTrash();
  const purge = usePurgeTrash();
  const [selected, setSelected] = useState<Set<number>>(new Set());

  const items = query.data?.items ?? [];
  const retention = query.data?.retention_days ?? 0;
  const isAdmin = can("admin");
  const canWrite = can("library:write");
  const mayAct = (ownerId: number | null) => isAdmin || (ownerId !== null && ownerId === me?.id);
  const actionable = items.filter((i) => mayAct(i.created_by_user_id));
  const chosen = actionable.filter((i) => selected.has(i.id));
  const busy = restore.isPending || purge.isPending;

  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  function doRestore(ids: number[]) {
    // Failures are toasted by the global MutationCache.onError.
    restore.mutate(ids, {
      onSuccess: (r) => {
        toast.success(`Restored ${plural(r.count)}`);
        setSelected(new Set());
      },
    });
  }

  function doPurge(ids: number[]) {
    purge.mutate(ids, {
      onSuccess: (r) => {
        toast.success(`Permanently deleted ${plural(r.count)}`);
        setSelected(new Set());
      },
    });
  }

  return (
    <PageContainer width="default">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-foreground">Trash</h1>
          <p className="text-sm text-muted-foreground">
            {retention > 0
              ? `Deleted models are kept for ${retention} day${retention === 1 ? "" : "s"}, then permanently removed.`
              : "Deleted models are kept until you purge them."}
          </p>
        </div>
        {canWrite && actionable.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={busy || chosen.length === 0}
              onClick={() => doRestore(chosen.map((i) => i.id))}
            >
              <RotateCcwIcon /> Restore{chosen.length > 0 ? ` (${chosen.length})` : ""}
            </Button>
            <ConfirmDialog
              trigger={
                <Button type="button" variant="destructive" disabled={busy || chosen.length === 0}>
                  <Trash2Icon /> Delete permanently{chosen.length > 0 ? ` (${chosen.length})` : ""}
                </Button>
              }
              title={`Permanently delete ${plural(chosen.length)}?`}
              description="Removes the selected models and every file they store from storage. This cannot be undone."
              confirmLabel="Delete permanently"
              destructive
              onConfirm={() => doPurge(chosen.map((i) => i.id))}
            />
            <ConfirmDialog
              trigger={
                <Button type="button" variant="destructive" disabled={busy}>
                  Empty trash
                </Button>
              }
              title={`Permanently delete ${plural(actionable.length)}?`}
              description="Empties the trash: removes these models and every file they store from storage. This cannot be undone."
              confirmLabel="Empty trash"
              destructive
              onConfirm={() => doPurge(actionable.map((i) => i.id))}
            />
          </div>
        )}
      </div>

      {query.isLoading ? (
        <Skeleton className="h-32 w-full rounded-lg" />
      ) : query.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {query.error instanceof ApiError ? query.error.detail : "Couldn't load the trash."}
        </p>
      ) : items.length === 0 ? (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            The trash is empty.
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {canWrite && actionable.length > 0 && (
            <label className="flex items-center gap-2 px-1 text-sm text-muted-foreground">
              <Checkbox
                aria-label="Select all"
                checked={chosen.length === actionable.length}
                onCheckedChange={(v) =>
                  setSelected(v === true ? new Set(actionable.map((i) => i.id)) : new Set())
                }
              />
              Select all
            </label>
          )}
          {items.map((item) => {
            const may = canWrite && mayAct(item.created_by_user_id);
            return (
              <Card key={item.id}>
                <CardContent className="flex items-center gap-3 py-3">
                  {may ? (
                    <Checkbox
                      aria-label={`Select ${item.name}`}
                      checked={selected.has(item.id)}
                      onCheckedChange={() => toggle(item.id)}
                    />
                  ) : null}
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">{item.name}</div>
                    <div className="text-xs text-muted-foreground">
                      Deleted {formatDateTime(item.deleted_at)}
                      {item.deleted_by ? ` by ${item.deleted_by}` : ""} · {item.file_count} file
                      {item.file_count === 1 ? "" : "s"} · {humanizeBytes(item.size_bytes)}
                      {item.purge_at ? ` · purged ${formatDateTime(item.purge_at)}` : ""}
                    </div>
                  </div>
                  {may ? (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      onClick={() => doRestore([item.id])}
                    >
                      <RotateCcwIcon /> Restore
                    </Button>
                  ) : null}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        <Link to="/library" className="underline">
          Back to library
        </Link>
      </p>
    </PageContainer>
  );
}
