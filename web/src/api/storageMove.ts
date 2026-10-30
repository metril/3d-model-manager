/** Folder move (C13): `POST /storage/folders/move` starts a job; resolve when it finishes. */
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { JobOut } from "@/api/types";

const POLL_MS = 700;
const MAX_POLLS = 120;

async function waitForJob(job: JobOut): Promise<void> {
  let cur = job;
  for (let i = 0; i < MAX_POLLS && cur.state !== "done" && cur.state !== "failed"; i += 1) {
    await new Promise((r) => setTimeout(r, POLL_MS));
    const jobs = await api.get<JobOut[]>("/jobs?limit=200");
    cur = jobs.find((j) => j.id === job.id) ?? cur;
  }
  if (cur.state === "failed") throw new Error(cur.error ?? "move failed");
}

export function useMoveFolder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ src, dest }: { src: string; dest: string }) =>
      waitForJob(await api.post<JobOut>("/storage/folders/move", { src, dest })),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["storage"] }),
  });
}

/** Mirror of the server rule (`plan_folder_move`): folders move only inside their own revision. */
export function canMoveFolder(src: string, destParent: string): boolean {
  const s = src.split("/").filter(Boolean);
  const d = destParent.split("/").filter(Boolean);
  if (s.length < 3 || d.length < 2 || s[0] !== d[0] || s[1] !== d[1]) return false;
  const dest = d.join("/");
  const from = s.join("/");
  return dest !== from && !dest.startsWith(`${from}/`) && dest !== s.slice(0, -1).join("/");
}
