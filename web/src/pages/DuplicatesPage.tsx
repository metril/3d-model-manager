/**
 * Duplicate-files report (Branch 4 Task 2): files sharing a blob hash across
 * more than one model -- reclaimable storage from the same content having
 * been imported/uploaded more than once. Each row offers a confirm-gated
 * delete that reuses the model Files tab's own `DELETE /files/{id}`
 * mutation (`useDeleteFile`), then invalidates both this report and the
 * affected model's detail/files query.
 *
 * Round 11 T4 adds group resolution: each group picks a "keeper" copy (a
 * radio per row, defaulting to the report's own lowest-model-id ordering --
 * `group.files[0]`) and "Delete extras" (per group) / "Delete all
 * duplicates" (page-wide) delete every OTHER current-revision copy in one
 * call via `POST /reports/duplicates/resolve` (`useResolveDuplicates`).
 * Old-revision copies stay keeper-eligible (keeping one = doing nothing)
 * but are excluded from deletable counts -- the server would skip them
 * anyway (file ops are current-revision-only, same guard as `DELETE
 * /files/{id}`), so the UI shouldn't promise their deletion.
 */
import { useTranslation } from "react-i18next";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronDownIcon, ChevronRightIcon, CopyCheckIcon, Trash2Icon } from "lucide-react";
import type { TFunction } from "i18next";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useDeleteFile } from "@/api/library";
import { duplicatesReportQueryOptions, useDuplicatesReport, useResolveDuplicates } from "@/api/reports";
import type { DuplicateFile, DuplicateGroup, KeepChoice } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ExpandCollapseAll } from "@/components/ExpandCollapseAll";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { PageContainer } from "@/components/ui/page-container";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Skeleton } from "@/components/ui/skeleton";
import { humanizeBytes } from "@/lib/format";
import { useOpenMap } from "@/lib/useOpenMap";

/** `1 copy` / `2 copies`. */
function copyCount(t: TFunction, n: number): string {
  return t("duplicatesPage.copies", { count: n });
}

/** Default keeper for a group is its lowest-model-id file (the report
 * already sorts `group.files` by `(model_id, file_id)`) unless the user
 * picked a different one -- purely derived from `keepers` + `group` so a
 * refetch with changed groups re-defaults sanely without an effect. A
 * stored choice that is no longer IN the group (its row was deleted, e.g.
 * via the per-row trash, and the report refetched) falls back to the
 * default -- honoring it would leave no radio checked, promise an inflated
 * delete count, and 404 the resolve request. */
function keeperFor(group: DuplicateGroup, keepers: Record<string, number>): number {
  const stored = keepers[group.blob_hash];
  return stored !== undefined && group.files.some((file) => file.file_id === stored)
    ? stored
    : group.files[0].file_id;
}

/** Deletable copies in a group: current-revision files other than the
 * keeper. Old-revision copies are never counted -- the server would skip
 * them (file ops are current-revision-only), so the UI shouldn't promise
 * their deletion. */
function deletableCount(group: DuplicateGroup, keeper: number): number {
  return group.files.filter((file) => file.is_current_revision && file.file_id !== keeper).length;
}

export function DuplicatesPage() {
  const { t } = useTranslation();
  const reportQuery = useDuplicatesReport();
  const groups = reportQuery.data?.groups ?? [];
  const [keepers, setKeepers] = useState<Record<string, number>>({});
  const resolve = useResolveDuplicates();
  const { isOpen, toggle, openAll, closeAll, allOpen, allClosed } = useOpenMap(
    groups.map((group) => group.blob_hash),
    true,
  );

  const totalDeletable = groups.reduce((sum, group) => sum + deletableCount(group, keeperFor(group, keepers)), 0);

  function handleResolve(choices: KeepChoice[]) {
    // No local onError: queryClient.ts's global MutationCache.onError
    // already toasts the ApiError detail for every failed mutation.
    resolve.mutate(choices, {
      onSuccess: (result) => {
        toast.success(
          t("duplicatesPage.deleted", { copies: copyCount(t, result.deleted), size: humanizeBytes(result.reclaimed_bytes) }),
        );
        // Old-revision copies were never promised (excluded from the
        // dialog's count and badged in the list) -- warning about them
        // would just contradict the UI. Only surface skips the user
        // couldn't see coming.
        const unexpected = result.skipped.filter((entry) => entry.reason !== "not_current_revision");
        if (unexpected.length > 0) {
          toast.warning(
            t("duplicatesPage.skipped", { copies: copyCount(t, unexpected.length) }),
          );
        }
      },
    });
  }

  return (
    <PageContainer width="default">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">{t("duplicatesPage.title")}</h1>
          <p className="text-sm text-muted-foreground">
            {reportQuery.data
              ? t("duplicatesPage.reclaimable", { size: humanizeBytes(reportQuery.data.total_wasted_bytes) })
              : t("duplicatesPage.subtitle")}
          </p>
        </div>
        {groups.length > 0 && (
          <div className="flex items-center gap-2">
            <ExpandCollapseAll
              label={t("duplicatesPage.groups")}
              allOpen={allOpen}
              allClosed={allClosed}
              onExpandAll={openAll}
              onCollapseAll={closeAll}
            />
            <ConfirmDialog
              trigger={
                <Button
                  type="button"
                  variant="destructive"
                  disabled={resolve.isPending || totalDeletable === 0}
                >
                  <Trash2Icon />
                  {t("duplicatesPage.deleteAll")}
                </Button>
              }
              title={t("duplicatesPage.resolveTitle", { count: totalDeletable })}
              description={t("duplicatesPage.resolveDesc")}
              confirmLabel={t("common.delete")}
              destructive
              onConfirm={() =>
                handleResolve(
                  groups.map((group) => ({ blob_hash: group.blob_hash, file_id: keeperFor(group, keepers) })),
                )
              }
            />
          </div>
        )}
      </div>

      {reportQuery.isLoading ? (
        <Skeleton className="h-32 w-full rounded-lg" />
      ) : reportQuery.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {reportQuery.error instanceof ApiError ? reportQuery.error.detail : t("duplicatesPage.loadError")}
        </p>
      ) : groups.length === 0 ? (
        <EmptyState
          icon={CopyCheckIcon}
          title={t("duplicatesPage.none")}
          action={
            <Button asChild variant="outline" size="sm">
              <Link to="/library">{t("trashPage.back")}</Link>
            </Button>
          }
        />
      ) : (
        <div className="space-y-4">
          {groups.map((group) => (
            <DuplicateGroupCard
              key={group.blob_hash}
              group={group}
              keeper={keeperFor(group, keepers)}
              onKeeperChange={(fileId) => setKeepers((prev) => ({ ...prev, [group.blob_hash]: fileId }))}
              resolvePending={resolve.isPending}
              onResolve={handleResolve}
              open={isOpen(group.blob_hash)}
              onToggle={() => toggle(group.blob_hash)}
            />
          ))}
        </div>
      )}
    </PageContainer>
  );
}

function DuplicateGroupCard({
  group,
  keeper,
  onKeeperChange,
  resolvePending,
  onResolve,
  open,
  onToggle,
}: {
  group: DuplicateGroup;
  keeper: number;
  onKeeperChange: (fileId: number) => void;
  resolvePending: boolean;
  onResolve: (choices: KeepChoice[]) => void;
  open: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  const deletable = deletableCount(group, keeper);

  return (
    <Card>
      <CardHeader className="flex flex-row items-start gap-2">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-start gap-2 text-left"
          aria-expanded={open}
          onClick={onToggle}
        >
          {open ? (
            <ChevronDownIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRightIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          )}
          <div className="min-w-0">
            <CardTitle className="truncate font-mono text-sm">{group.blob_hash.slice(0, 12)}</CardTitle>
            <CardDescription>
              {t("duplicatesPage.groupMeta", {
                size: humanizeBytes(group.size),
                wasted: humanizeBytes(group.wasted_bytes),
                count: group.files.length,
              })}
            </CardDescription>
          </div>
        </button>
        <ConfirmDialog
          trigger={
            <Button type="button" variant="outline" size="sm" disabled={deletable === 0 || resolvePending}>
              {t("duplicatesPage.deleteExtras")}
            </Button>
          }
          title={t("duplicatesPage.resolveTitle", { count: deletable })}
          description={t("duplicatesPage.resolveDesc")}
          confirmLabel={t("common.delete")}
          destructive
          onConfirm={() => onResolve([{ blob_hash: group.blob_hash, file_id: keeper }])}
        />
      </CardHeader>
      {open ? (
        <CardContent>
          <RadioGroup
            asChild
            className="gap-1.5"
            value={String(keeper)}
            onValueChange={(value) => onKeeperChange(Number(value))}
          >
            <ul>
              {group.files.map((file) => (
                <DuplicateFileRow key={file.file_id} file={file} />
              ))}
            </ul>
          </RadioGroup>
        </CardContent>
      ) : null}
    </Card>
  );
}

function DuplicateFileRow({ file }: { file: DuplicateFile }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  // Same mutation the model's Files tab uses (`useDeleteFile`, keyed to
  // THIS file's own model -- duplicates by definition span different
  // models, so each row needs its own hook instance/slug).
  const deleteFile = useDeleteFile(file.model_slug);
  const radioId = `keeper-${file.file_id}`;

  function handleConfirm() {
    deleteFile.mutate(file.file_id, {
      onSuccess: () => void queryClient.invalidateQueries({ queryKey: duplicatesReportQueryOptions.queryKey }),
    });
  }

  return (
    <li className="flex items-center justify-between gap-2 text-sm">
      <div className="flex min-w-0 items-center gap-2">
        <div className="flex shrink-0 items-center gap-1.5">
          <RadioGroupItem
            value={String(file.file_id)}
            id={radioId}
            aria-label={t("duplicatesPage.keepFile", { model: file.model_name, file: file.file_name })}
          />
          <Label htmlFor={radioId} className="text-xs font-normal text-muted-foreground">
            {t("duplicatesPage.keep")}
          </Label>
        </div>
        <div className="min-w-0">
          <Link to="/models/$slug" params={{ slug: file.model_slug }} className="hover:underline">
            {file.model_name} — {file.file_name}
          </Link>
          {file.model_archived && <span className="text-muted-foreground"> {t("duplicatesPage.archived")}</span>}
          {!file.is_current_revision && (
            <Badge variant="outline" className="ml-2 align-middle">
              {t("duplicatesPage.oldRevision")}
            </Badge>
          )}
          {deleteFile.isError ? (
            <p role="alert" className="text-xs text-destructive">
              {deleteFile.error instanceof ApiError ? deleteFile.error.detail : t("duplicatesPage.deleteError")}
            </p>
          ) : null}
        </div>
      </div>
      <ConfirmDialog
        trigger={
          <Button
            type="button"
            variant="destructive"
            size="icon-sm"
            aria-label={t("duplicatesPage.deleteFile", { file: file.file_name })}
            disabled={deleteFile.isPending}
          >
            <Trash2Icon />
          </Button>
        }
        title={t("duplicatesPage.copyTitle")}
        description={t("duplicatesPage.copyDesc", { file: file.file_name, model: file.model_name })}
        confirmLabel={t("common.delete")}
        destructive
        onConfirm={handleConfirm}
      />
    </li>
  );
}
