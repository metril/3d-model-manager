/** File management (rename / move / delete) on a model's current revision.
 * Pure planning helpers + mutations over `PATCH /files/{id}`,
 * `POST /models/{id}/files/move` and `DELETE /files/{id}`. */
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { modelQueryOptions } from "@/api/library";
import type { FileOut, JobOut } from "@/api/types";
import { JobTimeoutError, waitForJob } from "@/api/waitForJob";
import { baseName, dirName, isInside } from "@/components/model-detail/explorer/tree";
import { joinPath } from "@/components/model-detail/explorer/treeEdit";

export interface FileMove {
  file_id: number;
  rel_path: string;
}

export type NameError = "empty" | "slash" | "dot" | "reserved" | "clash";

/** Client mirror of the server's path rules plus a case-insensitive sibling clash check.
 * `siblings` are the other names in the same parent (exclude the entry being renamed). */
export function validateName(raw: string, siblings: readonly string[]): NameError | null {
  const name = raw.trim();
  if (name === "") return "empty";
  if (name.includes("/") || name.includes("\\")) return "slash";
  if (name === "." || name === "..") return "dot";
  if (name.toLowerCase() === "_snapshots") return "reserved";
  const lower = name.toLowerCase();
  if (siblings.some((s) => s.toLowerCase() === lower)) return "clash";
  return null;
}

export type DragItems = { fileIds: number[]; folders: string[] };

export interface MovePlan {
  moves: FileMove[];
  /** Folder relocations (for rewriting client-only empty folders). */
  folderMoves: { from: string; to: string }[];
  error: "into-self" | "clash" | null;
  clashPath?: string;
}

/** Plans the moves for relocating files to `fileTargets` (id -> new path) and
 * folders to `folderTargets` (old path -> new path). Unchanged paths are dropped. */
export function planRelocation(
  allFiles: readonly FileOut[],
  fileTargets: ReadonlyMap<number, string>,
  folderTargets: ReadonlyMap<string, string>,
): MovePlan {
  const folderMoves: MovePlan["folderMoves"] = [];
  for (const [from, to] of folderTargets) {
    if (to !== from && isInside(to, from)) return { moves: [], folderMoves: [], error: "into-self" };
    folderMoves.push({ from, to });
  }
  const finalPath = new Map<number, string>();
  for (const file of allFiles) {
    const direct = fileTargets.get(file.id);
    if (direct !== undefined) {
      finalPath.set(file.id, direct);
      continue;
    }
    for (const { from, to } of folderMoves) {
      if (isInside(file.rel_path, from)) {
        finalPath.set(file.id, joinPath(to, file.rel_path.slice(from.length)));
        break;
      }
    }
  }
  // Mirror the server's case-insensitive checks on the FINAL layout: no two files on one path,
  // no file at another file's ancestor path, no file under another file's path.
  const holders = new Map<string, number>();
  const ancestors = new Set<string>();
  const finalOf = (file: FileOut) => finalPath.get(file.id) ?? file.rel_path;
  for (const file of allFiles) {
    const lower = finalOf(file).toLowerCase();
    const prior = holders.get(lower);
    if (prior !== undefined && prior !== file.id && (finalPath.has(file.id) || finalPath.has(prior))) {
      return { moves: [], folderMoves, error: "clash", clashPath: finalOf(file) };
    }
    holders.set(lower, file.id);
    for (let i = lower.indexOf("/"); i !== -1; i = lower.indexOf("/", i + 1)) ancestors.add(lower.slice(0, i));
  }
  const moves: FileMove[] = [];
  for (const file of allFiles) {
    const to = finalPath.get(file.id);
    if (to === undefined) continue;
    const lower = to.toLowerCase();
    let clash = ancestors.has(lower);
    for (let i = lower.indexOf("/"); !clash && i !== -1; i = lower.indexOf("/", i + 1)) {
      const holder = holders.get(lower.slice(0, i));
      clash = holder !== undefined && holder !== file.id;
    }
    if (clash) return { moves: [], folderMoves, error: "clash", clashPath: to };
    if (to !== file.rel_path) moves.push({ file_id: file.id, rel_path: to });
  }
  return { moves, folderMoves: folderMoves.filter((m) => m.from !== m.to), error: null };
}

export function planRenameFile(allFiles: readonly FileOut[], file: FileOut, newName: string): MovePlan {
  return planRelocation(allFiles, new Map([[file.id, joinPath(dirName(file.rel_path), newName.trim())]]), new Map());
}

export function planRenameFolder(allFiles: readonly FileOut[], folder: string, newName: string): MovePlan {
  return planRelocation(allFiles, new Map(), new Map([[folder, joinPath(dirName(folder), newName.trim())]]));
}

/** Moves dragged/selected files and folders into `dest` (keeping their names). */
export function planMoveInto(allFiles: readonly FileOut[], items: DragItems, dest: string): MovePlan {
  const fileTargets = new Map<number, string>();
  for (const id of items.fileIds) {
    const file = allFiles.find((f) => f.id === id);
    if (file) fileTargets.set(id, joinPath(dest, baseName(file.rel_path)));
  }
  const folderTargets = new Map<string, string>();
  for (const folder of items.folders) folderTargets.set(folder, joinPath(dest, baseName(folder)));
  return planRelocation(allFiles, fileTargets, folderTargets);
}

/** True when `dest` can't take the items: a folder into itself/descendant, or nothing would change. */
export function isMoveTargetDisabled(items: DragItems, dest: string, allFiles: readonly FileOut[]): boolean {
  if (items.folders.some((f) => isInside(dest, f))) return true;
  const plan = planMoveInto(allFiles, items, dest);
  return plan.error !== null || (plan.moves.length === 0 && plan.folderMoves.length === 0);
}

function invalidate(qc: ReturnType<typeof useQueryClient>, slug: string) {
  void qc.invalidateQueries({ queryKey: modelQueryOptions(slug).queryKey });
  void qc.invalidateQueries({ queryKey: ["storage"] });
}

/** Server-side cap per batch request. */
export const MOVE_CHUNK = 1000;

/** Thrown when a later chunk wasn't submitted because an earlier job outlived the polling window. */
export class MoveStillRunning extends JobTimeoutError {
  readonly remaining: number;
  constructor(remaining: number) {
    super("move");
    this.remaining = remaining;
  }
}

/** One move uses `PATCH /files/{id}`, several use the batch endpoint; resolves when the job finishes. */
export function useMoveFiles(model: { id: number; slug: string }) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (moves: FileMove[]) => {
      // Chunks run one after another: concurrent jobs on one revision would trip the store-pending 409.
      for (let i = 0; i < moves.length; i += MOVE_CHUNK) {
        const chunk = moves.slice(i, i + MOVE_CHUNK);
        const job =
          moves.length === 1
            ? await api.patch<JobOut>(`/files/${chunk[0].file_id}`, { rel_path: chunk[0].rel_path })
            : await api.post<JobOut>(`/models/${model.id}/files/move`, { moves: chunk });
        try {
          await waitForJob(job, "move");
        } catch (e) {
          if (e instanceof JobTimeoutError) throw new MoveStillRunning(moves.length - (i + chunk.length));
          throw e;
        }
      }
    },
    onSettled: () => invalidate(qc, model.slug),
  });
}

/** Max `DELETE /files/{id}` requests in flight. */
export const DELETE_CONCURRENCY = 4;

export interface DeleteResult {
  ok: number;
  failed: number;
  firstError: unknown;
}

/** Deletes files one by one (`DELETE /files/{id}`); never rejects, reports per-file failures. */
export function useDeleteFiles(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (ids: number[]): Promise<DeleteResult> => {
      let next = 0;
      let failed = 0;
      let firstError: unknown;
      const worker = async () => {
        while (next < ids.length) {
          const id = ids[next++];
          try {
            await api.delete<void>(`/files/${id}`);
          } catch (e) {
            failed += 1;
            firstError ??= e;
          }
        }
      };
      await Promise.all(Array.from({ length: Math.min(DELETE_CONCURRENCY, ids.length) }, worker));
      return { ok: ids.length - failed, failed, firstError };
    },
    onSettled: () => invalidate(qc, slug),
  });
}
