/** Shared job poller: resolves once the job is `done`, throws if it fails or never finishes. */
import { api } from "@/api/client";
import type { JobOut } from "@/api/types";

export const POLL_MS = 700;
export const MAX_POLLS = 120;

/** The job is still queued/running after the polling window; it keeps going server-side. */
export class JobTimeoutError extends Error {
  constructor(what: string) {
    super(`${what} is taking too long; check the Jobs page`);
    this.name = "JobTimeoutError";
  }
}

export async function waitForJob(job: JobOut, what = "move"): Promise<void> {
  let cur = job;
  for (let i = 0; i < MAX_POLLS && cur.state !== "done" && cur.state !== "failed" && cur.state !== "dead"; i += 1) {
    await new Promise((r) => setTimeout(r, POLL_MS));
    cur = await api.get<JobOut>(`/jobs/${job.id}`);
  }
  if (cur.state === "failed" || cur.state === "dead") throw new Error(cur.error ?? `${what} failed`);
  if (cur.state !== "done") throw new JobTimeoutError(what);
}
