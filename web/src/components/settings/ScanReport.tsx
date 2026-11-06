/**
 * Scan section on Settings → Storage (Task 8): trigger a library scan and
 * show the latest run's counters plus its adopted/relinked/changed/missing/
 * errors lists. Missing rows resolve via the existing `DELETE /api/files/{id}`
 * endpoint (reusing `useDeleteFile`, same as `FilesTab`) rather than a new
 * resolution state machine -- the Task 8 brief's explicit call.
 */
import { useTranslation } from "react-i18next";
import type { ReactNode } from "react";
import { ChevronDownIcon, ChevronRightIcon, Trash2Icon } from "lucide-react";
import { Link } from "@tanstack/react-router";

import { ApiError } from "@/api/client";
import { useDeleteFile } from "@/api/library";
import { useScanRuns, useTriggerScan } from "@/api/scan";
import type { ScanChanged, ScanError, ScanMissing, ScanRelinked, ScanRunOut, ScanState, ScanAdopted } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ExpandCollapseAll } from "@/components/ExpandCollapseAll";
import { CopyableHash } from "@/components/model-detail/CopyableHash";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateTime } from "@/lib/format";
import { useOpenMap } from "@/lib/useOpenMap";

const STATE_VARIANT: Record<ScanState, "default" | "secondary" | "outline" | "destructive"> = {
  queued: "outline",
  running: "secondary",
  done: "default",
  failed: "destructive",
  skipped: "outline",
};

const IN_FLIGHT_STATES: ScanState[] = ["queued", "running"];

function Counter({ label, value }: { label: string; value: number }) {
  return (
    <span className="text-xs text-muted-foreground">
      {label}: <span className="font-medium text-foreground">{value}</span>
    </span>
  );
}

function CountersRow({ run }: { run: ScanRunOut }) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 rounded-lg bg-muted/40 p-3">
      <Badge variant={STATE_VARIANT[run.state]}>{run.state}</Badge>
      <span className="text-xs text-muted-foreground">{formatDateTime(run.created_at)}</span>
      <Counter label={t("settingsScan.seen")} value={run.files_seen} />
      <Counter label={t("settingsScan.hashed")} value={run.files_hashed} />
      <Counter label={t("settingsScan.relinked")} value={run.relinked} />
      <Counter label={t("settingsScan.adopted")} value={run.adopted} />
      <Counter label={t("settingsScan.missing")} value={run.missing} />
    </div>
  );
}

/** A collapsible section (Round 11: controlled via `useOpenMap`/`open`+
 * `onToggle` rather than an uncontrolled `<details>`, so the parent can
 * offer an expand-all/collapse-all control -- kept as a bordered div with a
 * `<button aria-expanded>` header rather than pulling in a new shadcn
 * Accordion dependency; none of the existing `components/ui/*` cover it,
 * and the Global Constraints ledger already flags this app's bundle
 * weight). */
function Section({
  label,
  count,
  empty,
  open,
  onToggle,
  children,
}: {
  label: string;
  count: number;
  empty: string;
  open: boolean;
  onToggle: () => void;
  children?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-sm font-semibold text-foreground select-none"
        aria-expanded={open}
        onClick={onToggle}
      >
        <span>
          {label} ({count})
        </span>
        {open ? (
          <ChevronDownIcon className="size-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRightIcon className="size-4 shrink-0 text-muted-foreground" />
        )}
      </button>
      {open && (
        <div className="border-t border-border px-3 py-2">
          {count === 0 ? (
            <p className="text-xs text-muted-foreground">{empty}</p>
          ) : (
            <ul className="space-y-1.5">{children}</ul>
          )}
        </div>
      )}
    </div>
  );
}

// Links to the model page rather than surfacing a `review_state` badge
// inline -- `ModelSummary`/`ModelDetail` don't return that field today
// (only `ModelPatch` accepts it to clear it), and adding it is a backend
// schema change outside this frontend task's file list (Task 8 brief's
// documented fallback: "else just link").
function AdoptedRow({ entry }: { entry: ScanAdopted }) {
  const { t } = useTranslation();
  return (
    <li className="text-sm">
      <Link to="/models/$slug" params={{ slug: entry.slug }} className="underline">
        {entry.slug}
      </Link>
      <span className="ml-2 text-xs text-muted-foreground">
        {t("settingsScan.fileCount", { count: entry.files.length })}
      </span>
    </li>
  );
}

function RelinkedRow({ entry }: { entry: ScanRelinked }) {
  return (
    <li className="flex flex-wrap items-center gap-1.5 text-sm">
      <span className="font-mono text-xs">{entry.from}</span>
      <span className="text-muted-foreground">→</span>
      <span className="font-mono text-xs">{entry.to}</span>
      <CopyableHash hash={entry.hash} />
    </li>
  );
}

function ChangedRow({ entry }: { entry: ScanChanged }) {
  return (
    <li className="flex flex-wrap items-center gap-1.5 text-sm">
      <span className="font-mono text-xs">{entry.storage_path}</span>
      <CopyableHash hash={entry.old_hash} />
      <span className="text-muted-foreground">→</span>
      <CopyableHash hash={entry.new_hash} />
    </li>
  );
}

function ErrorRow({ entry }: { entry: ScanError }) {
  return (
    <li className="text-sm">
      <span className="font-mono text-xs">{entry.storage_path}</span>
      <span className="text-muted-foreground">: </span>
      <span className="text-destructive">{entry.error}</span>
    </li>
  );
}

function MissingRow({ missing }: { missing: ScanMissing }) {
  const { t } = useTranslation();
  const deleteFile = useDeleteFile(missing.model_slug);

  return (
    <li className="space-y-1 rounded-md border border-border/60 p-2">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate font-mono text-xs" title={missing.storage_path}>
            {missing.storage_path}
          </p>
          <Link to="/models/$slug" params={{ slug: missing.model_slug }} className="text-xs text-muted-foreground underline">
            {missing.model_slug}
          </Link>
        </div>
        <ConfirmDialog
          trigger={
            <Button type="button" variant="outline" size="sm" disabled={deleteFile.isPending}>
              <Trash2Icon className="size-3.5" />
              {t("settingsScan.removeRecord")}
            </Button>
          }
          title={t("settingsScan.removeTitle")}
          description={t("settingsScan.removeDesc", { path: missing.storage_path })}
          confirmLabel={t("common.remove")}
          destructive
          onConfirm={() => deleteFile.mutate(missing.file_id)}
        />
      </div>
      {deleteFile.isError ? (
        <p role="alert" className="text-xs text-destructive">
          {deleteFile.error instanceof ApiError ? deleteFile.error.detail : t("settingsScan.removeError")}
        </p>
      ) : null}
      {deleteFile.isSuccess ? <p className="text-xs text-success">{t("settingsScan.removed")}</p> : null}
    </li>
  );
}

const SECTION_IDS = ["adopted", "relinked", "changed", "missing", "errors"] as const;
type SectionId = (typeof SECTION_IDS)[number];

function LatestRun({ run }: { run: ScanRunOut }) {
  const { t } = useTranslation();
  const adopted = run.report?.adopted ?? [];
  const relinked = run.report?.relinked ?? [];
  const changed = run.report?.changed ?? [];
  const missing = run.report?.missing ?? [];
  const errors = run.report?.errors ?? [];

  const counts: Record<SectionId, number> = {
    adopted: adopted.length,
    relinked: relinked.length,
    changed: changed.length,
    missing: missing.length,
    errors: errors.length,
  };
  // Preserves the old uncontrolled `<details open={count > 0}>` default:
  // a section with entries starts open, an empty one starts closed -- but
  // as an explicit per-id default rather than a fixed initial attribute, so
  // openAll/closeAll can override it.
  const { isOpen, toggle, openAll, closeAll, allOpen, allClosed } = useOpenMap<SectionId>(
    SECTION_IDS,
    (id) => counts[id] > 0,
  );

  return (
    <div className="space-y-3">
      <CountersRow run={run} />
      <div className="flex justify-end">
        <ExpandCollapseAll
          label={t("settingsScan.sections")}
          allOpen={allOpen}
          allClosed={allClosed}
          onExpandAll={openAll}
          onCollapseAll={closeAll}
        />
      </div>
      <div className="space-y-2">
        <Section
          label={t("settingsScan.adopted")}
          count={adopted.length}
          empty={t("settingsScan.emptyAdopted")}
          open={isOpen("adopted")}
          onToggle={() => toggle("adopted")}
        >
          {adopted.map((entry) => (
            <AdoptedRow key={`${entry.model_id}-${entry.revision_id}`} entry={entry} />
          ))}
        </Section>
        <Section
          label={t("settingsScan.relinked")}
          count={relinked.length}
          empty={t("settingsScan.emptyRelinked")}
          open={isOpen("relinked")}
          onToggle={() => toggle("relinked")}
        >
          {relinked.map((entry) => (
            <RelinkedRow key={entry.file_id} entry={entry} />
          ))}
        </Section>
        <Section
          label={t("settingsScan.changed")}
          count={changed.length}
          empty={t("settingsScan.emptyChanged")}
          open={isOpen("changed")}
          onToggle={() => toggle("changed")}
        >
          {changed.map((entry) => (
            <ChangedRow key={entry.file_id} entry={entry} />
          ))}
        </Section>
        <Section
          label={t("settingsScan.missing")}
          count={missing.length}
          empty={t("settingsScan.emptyMissing")}
          open={isOpen("missing")}
          onToggle={() => toggle("missing")}
        >
          {missing.map((entry) => (
            <MissingRow key={entry.file_id} missing={entry} />
          ))}
        </Section>
        <Section
          label={t("settingsScan.errors")}
          count={errors.length}
          empty={t("settingsScan.emptyErrors")}
          open={isOpen("errors")}
          onToggle={() => toggle("errors")}
        >
          {errors.map((entry, index) => (
            <ErrorRow key={`${entry.storage_path}-${index}`} entry={entry} />
          ))}
        </Section>
      </div>
    </div>
  );
}

export function ScanReport() {
  const { t } = useTranslation();
  const scanRunsQuery = useScanRuns();
  const triggerScan = useTriggerScan();

  const latestRun = scanRunsQuery.data?.[0];
  const isBusy = latestRun !== undefined && IN_FLIGHT_STATES.includes(latestRun.state);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settingsScan.title")}</CardTitle>
        <CardDescription>{t("settingsScan.description")}</CardDescription>
        <CardAction>
          <Button type="button" onClick={() => triggerScan.mutate()} disabled={triggerScan.isPending || isBusy}>
            {triggerScan.isPending || isBusy ? t("settingsScan.scanning") : t("settingsScan.run")}
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-4">
        {triggerScan.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {triggerScan.error instanceof ApiError ? triggerScan.error.detail : t("settingsScan.startError")}
          </p>
        ) : null}

        {scanRunsQuery.isLoading ? (
          <Skeleton className="h-24 w-full rounded-lg" />
        ) : scanRunsQuery.isError ? (
          <div className="space-y-2 text-center">
            <p role="alert" className="text-sm text-destructive">
              {scanRunsQuery.error instanceof ApiError ? scanRunsQuery.error.detail : t("settingsScan.historyError")}
            </p>
            <Button type="button" variant="outline" onClick={() => void scanRunsQuery.refetch()}>
              {t("common.retry")}
            </Button>
          </div>
        ) : !latestRun ? (
          <p className="py-4 text-center text-sm text-muted-foreground">
            {t("settingsScan.none")}
          </p>
        ) : (
          <LatestRun run={latestRun} />
        )}
      </CardContent>
    </Card>
  );
}
