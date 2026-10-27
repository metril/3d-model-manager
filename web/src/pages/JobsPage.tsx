/**
 * Jobs page (M6 Task 9): lists every background job tracked in `jobs`
 * (uploads, pipeline steps, scans, imports, storage migrations, ...),
 * replacing the earlier `<ComingSoonPage title="Jobs" />` stub. Table
 * conventions mirror `PrintJobHistory.tsx` (Badge per state, truncated
 * error cell with a `title` tooltip, `Skeleton` while loading, empty-state
 * copy). Live-updates via `useEvents.tsx`'s `job.updated` branch, which
 * invalidates `["jobs"]` on every transition -- no polling needed here.
 */
import { useState } from "react";

import { ApiError } from "@/api/client";
import { useJobs, useRetryJob } from "@/api/jobs";
import type { JobOut, JobState } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageContainer } from "@/components/ui/page-container";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime } from "@/lib/format";

const STATE_VARIANT: Record<JobState, "default" | "secondary" | "outline" | "destructive"> = {
  queued: "outline",
  running: "secondary",
  done: "default",
  failed: "destructive",
  dead: "destructive",
};

// The backend's `state` filter (`app/api/jobs.py`'s `GET /jobs?state=`)
// only ever takes a single value -- there's no way to ask it for "failed OR
// dead" in one request. "attention" and "all" are client-only pseudo-
// filters layered on top of that: both fetch the full (unfiltered) list and
// differ only in how this page narrows it afterward; every other value is
// passed straight through as `?state=`.
type StateFilter = "attention" | "all" | JobState;

const FILTER_OPTIONS: { value: StateFilter; label: string }[] = [
  { value: "attention", label: "Needs attention (failed + dead)" },
  { value: "all", label: "All" },
  { value: "queued", label: "Queued" },
  { value: "running", label: "Running" },
  { value: "done", label: "Done" },
  { value: "failed", label: "Failed" },
  { value: "dead", label: "Dead-lettered" },
];

const NEEDS_ATTENTION = new Set<JobState>(["failed", "dead"]);

/** A job counts as dead-lettered either because the backend already parked
 * it there (`state === "dead"`, Task 8's auto-park), or defensively, if it
 * has exhausted `max_attempts` but is caught in a transient state that
 * hasn't been persisted as `dead` yet. */
function isDeadLettered(job: JobOut): boolean {
  return job.state === "dead" || job.attempts >= job.max_attempts;
}

export function JobsPage() {
  const [filter, setFilter] = useState<StateFilter>("attention");
  const backendState = filter === "attention" || filter === "all" ? undefined : filter;
  const jobsQuery = useJobs({ state: backendState });

  const allJobs = jobsQuery.data ?? [];
  const jobs = filter === "attention" ? allJobs.filter((job) => NEEDS_ATTENTION.has(job.state)) : allJobs;

  return (
    <PageContainer width="default">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Jobs</h1>
          <p className="text-sm text-muted-foreground">
            Every background job tracked by the app — uploads, pipeline steps, scans, imports, and storage migrations.
          </p>
        </div>
        <Select value={filter} onValueChange={(value) => setFilter(value as StateFilter)}>
          <SelectTrigger aria-label="Filter by state">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {FILTER_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Job list</CardTitle>
          <CardDescription>
            {filter === "attention" ? "Jobs that failed or were dead-lettered." : "Most recent jobs, newest first."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {jobsQuery.isLoading ? (
            <Skeleton className="h-32 w-full rounded-lg" />
          ) : jobsQuery.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {jobsQuery.error instanceof ApiError ? jobsQuery.error.detail : "Couldn't load jobs."}
            </p>
          ) : jobs.length === 0 ? (
            <p className="py-4 text-center text-sm text-muted-foreground">
              {filter === "attention" ? "No jobs need attention." : "No jobs match this filter."}
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Type</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead>Subject</TableHead>
                  <TableHead>Attempts</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead>Error</TableHead>
                  <TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {jobs.map((job) => (
                  <JobRow key={job.id} job={job} />
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </PageContainer>
  );
}

function JobRow({ job }: { job: JobOut }) {
  const retryJob = useRetryJob();
  const dead = isDeadLettered(job);
  // `migrate_storage` has no retry path at all (`app/services/jobs.py`
  // hard-409s it -- there's no payload column to replay it from) -- hide
  // the button rather than offer one that always fails.
  const canRetry = NEEDS_ATTENTION.has(job.state) && job.type !== "migrate_storage";
  const showMigrateHint = NEEDS_ATTENTION.has(job.state) && job.type === "migrate_storage";
  const isRetrying = retryJob.isPending && retryJob.variables === job.id;
  const retryFailed = retryJob.isError && retryJob.variables === job.id;

  return (
    <TableRow>
      <TableCell className="font-mono text-xs">{job.type}</TableCell>
      <TableCell>
        {dead ? (
          <Badge variant="destructive">Dead-lettered</Badge>
        ) : (
          <Badge variant={STATE_VARIANT[job.state]}>{job.state}</Badge>
        )}
      </TableCell>
      <TableCell className="text-sm text-muted-foreground">
        {job.subject_type && job.subject_id != null ? `${job.subject_type} #${job.subject_id}` : "—"}
      </TableCell>
      <TableCell className="text-sm">
        {job.attempts}/{job.max_attempts}
      </TableCell>
      <TableCell>{formatDateTime(job.updated_at)}</TableCell>
      <TableCell className="max-w-48 truncate text-destructive" title={job.error ?? undefined}>
        {job.error ?? ""}
      </TableCell>
      <TableCell>
        {canRetry ? (
          <div className="space-y-1">
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={isRetrying}
              onClick={() => retryJob.mutate(job.id)}
            >
              {isRetrying ? "Retrying…" : "Retry"}
            </Button>
            {retryFailed ? (
              <p role="alert" className="text-xs text-destructive">
                {retryJob.error instanceof ApiError ? retryJob.error.detail : "Retry failed."}
              </p>
            ) : null}
          </div>
        ) : showMigrateHint ? (
          <span className="text-xs text-muted-foreground">Re-run from Settings</span>
        ) : null}
      </TableCell>
    </TableRow>
  );
}
