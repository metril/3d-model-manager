/**
 * Where this model's current-revision files live (Workstream C task C4),
 * plus the "Move / Copy to backend" dialog that dispatches
 * `POST /models/{slug}/relocate`. `model.backends` is the PRIMARY-backend-
 * only summary `ModelDetail` carries (backend/app/services/library.py's
 * `_model_backends_summary`, from `files.backend_id`) -- it updates once a
 * "move" relocate job lands (`app.tasks.relocate` flips `backend_id`); a
 * "replicate" job leaves it unchanged (bytes now live on more than one
 * backend, but there's still exactly one PRIMARY).
 *
 * The dialog's `open` state is owned by `ModelHeader` (the "Move / Copy to
 * backend…" item in its overflow menu) and passed in as a controlled pair,
 * rather than this component rendering its own trigger button -- everything
 * else (target/mode selection, the relocate mutation, and the "Relocation
 * <state>" progress line) still lives here.
 *
 * No explicit query invalidation is wired here on relocate success: the
 * app-wide SSE handler (`useEvents.tsx`) already invalidates `["models"]`
 * on every job's terminal state, which is how this bar picks up a completed
 * relocate without polling itself. `useJob` below is only for the small
 * "Relocation <state>" progress line shown after dispatch.
 */
import { useTranslation } from "react-i18next";
import { useState } from "react";
import { Link } from "@tanstack/react-router";

import { ApiError } from "@/api/client";
import { useJob } from "@/api/jobs";
import { useRelocateModel } from "@/api/library";
import { useStorageBackends } from "@/api/settings";
import type { ModelDetail } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DialogClose, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

type RelocateMode = "move" | "replicate";

export function StorageLocationBar({
  model,
  open,
  onOpenChange,
}: {
  model: ModelDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [targetId, setTargetId] = useState("");
  const { t } = useTranslation();
  const [mode, setMode] = useState<RelocateMode>("move");
  const [jobId, setJobId] = useState<string | undefined>(undefined);

  const backendsQuery = useStorageBackends();
  const relocate = useRelocateModel(model.slug);
  const relocateJob = useJob(jobId);

  const currentBackendIds = new Set(model.backends.map((backend) => backend.id));
  const candidates = (backendsQuery.data ?? []).filter((backend) => !currentBackendIds.has(backend.id));

  function reset() {
    setTargetId("");
    setMode("move");
    relocate.reset();
  }

  function handleStart() {
    if (!targetId) return;
    relocate.mutate(
      { target_backend_id: Number(targetId), mode },
      {
        onSuccess: (job) => {
          setJobId(job.id);
          onOpenChange(false);
        },
      },
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-muted-foreground">{t("mdStorage.storedOn")}</span>
      {model.backends.length === 0 ? (
        <span className="text-xs text-muted-foreground">{t("mdStorage.unknown")}</span>
      ) : (
        model.backends.map((backend) => (
          <Badge key={backend.id} variant="outline">
            {backend.name}
          </Badge>
        ))
      )}

      <FormDialog
        open={open}
        onOpenChange={(next) => {
          onOpenChange(next);
          if (!next) reset();
        }}
        dirty={targetId !== "" || mode !== "move"}
        busy={relocate.isPending}
      >
        <DialogHeader>
          <DialogTitle>{t("mdStorage.title")}</DialogTitle>
          <DialogDescription>
            {t("mdStorage.desc")}
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-4 py-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="relocate-target">{t("mdStorage.target")}</Label>
            {/* `aria-label` on `Select` itself (in addition to the real
             * one on `SelectTrigger` below) is a no-op on the real Radix
             * root but lets a jsdom test's native-<select> stand-in find
             * this control by accessible name even with more than one
             * `<select>` on the page at once. */}
            <Select
              aria-label={t("mdStorage.target")}
              value={targetId}
              onValueChange={setTargetId}
              disabled={relocate.isPending}
            >
              <SelectTrigger id="relocate-target" aria-label={t("mdStorage.target")}>
                <SelectValue placeholder={t("mdStorage.choose")} />
              </SelectTrigger>
              <SelectContent>
                {candidates.map((backend) => (
                  <SelectItem key={backend.id} value={String(backend.id)}>
                    {backend.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {backendsQuery.isSuccess && candidates.length === 0 ? (
              <p className="text-xs text-muted-foreground">{t("mdStorage.none")}</p>
            ) : null}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="relocate-mode">{t("mdStorage.action")}</Label>
            <Select
              aria-label={t("mdStorage.action")}
              value={mode}
              onValueChange={(next) => setMode(next as RelocateMode)}
              disabled={relocate.isPending}
            >
              <SelectTrigger id="relocate-mode" aria-label={t("mdStorage.action")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="move">{t("mdStorage.move")}</SelectItem>
                <SelectItem value="replicate">{t("mdStorage.replicate")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {relocate.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {relocate.error instanceof ApiError ? relocate.error.detail : t("mdStorage.couldNotStart")}
            </p>
          ) : null}
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button type="button" variant="outline">
              {t("common.cancel")}
            </Button>
          </DialogClose>
          <Button type="button" disabled={!targetId || relocate.isPending} onClick={handleStart}>
            {relocate.isPending ? t("mdStorage.starting") : t("mdStorage.start")}
          </Button>
        </DialogFooter>
      </FormDialog>

      {jobId ? (
        <span className="text-xs text-muted-foreground">
          {relocateJob.data ? t("mdStorage.relocationState", { state: relocateJob.data.state }) : t("mdStorage.relocationStarted")} —{" "}
          <Link to="/system/jobs" className="underline">
            {t("mdStorage.viewJobs")}
          </Link>
        </span>
      ) : null}
    </div>
  );
}
