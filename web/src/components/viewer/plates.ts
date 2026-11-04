/**
 * Pure plate helpers + a tiny selection store shared by the viewer's
 * PlateSelector, the model-detail PlatePanel and the print/slice pickers.
 * No React-three here (viewer math lives in pure modules); the store is a
 * module-level map keyed by blob hash so unrelated components agree on the
 * selected plate without prop plumbing.
 */
import { useSyncExternalStore } from "react";

import { glbUrl } from "@/components/viewer/viewable";
import type { FileOut, PlateOut } from "@/api/types";

/** Plates worth offering a choice between: a selector needs at least two. */
export function selectablePlates(plates: PlateOut[] | null | undefined): PlateOut[] {
  return plates && plates.length >= 2 ? plates : [];
}

export const plateGlbUrl = (blobHash: string, plate: number) => `/api/blobs/${blobHash}/plates/${plate}/glb`;
export const plateThumbUrl = (blobHash: string, plate: number) => `/api/blobs/${blobHash}/plates/${plate}/thumb`;

/** "Plate 2 · Lid" (name omitted when empty). */
export function plateLabel(plate: Pick<PlateOut, "index" | "name">): string {
  return plate.name ? `Plate ${plate.index} · ${plate.name}` : `Plate ${plate.index}`;
}

export interface ViewerPlateFile {
  fileId: number;
  blobHash: string;
  label: string;
  plates: PlateOut[];
}

/** "dir/Benchy-1234567.3mf" -> "Benchy" (extension and trailing MakerWorld id dropped). */
export function fileLabel(relPath: string): string {
  const base = relPath.split("/").pop() ?? relPath;
  const stem = base.replace(/\.[^.]+$/, "");
  return stem.replace(/-\d+$/, "") || stem || base;
}

/** Every file the viewer's selector can drive: 2+ plates with some GLB.
 * CHECKED (shown) files first, then the rest; one entry per blob. */
export function viewerPlates(files: FileOut[], checkedIds?: ReadonlySet<number>): ViewerPlateFile[] {
  const ordered = checkedIds ? [...files.filter((f) => checkedIds.has(f.id)), ...files.filter((f) => !checkedIds.has(f.id))] : files;
  const out: ViewerPlateFile[] = [];
  for (const file of ordered) {
    const plates = selectablePlates(file.meta?.plates);
    if (plates.length > 0 && plates.some((p) => p.has_glb) && !out.some((o) => o.blobHash === file.blob_hash)) {
      out.push({ fileId: file.id, blobHash: file.blob_hash, label: fileLabel(file.rel_path ?? ""), plates });
    }
  }
  return out;
}

/** The entry the selector currently drives: the preferred blob if still offered, else the first. */
export function resolveActive(entries: ViewerPlateFile[], preferred: string | null): ViewerPlateFile | null {
  return entries.find((e) => e.blobHash === preferred) ?? entries[0] ?? null;
}

/** File ids to render. A selected plate isolates the active blob's file(s);
 * no plate ("All plates") keeps the normal multi-file checked view. */
export function visibleParts(
  files: Pick<FileOut, "id" | "blob_hash">[],
  checkedIds: ReadonlySet<number>,
  activeBlob: string | null,
  selectedPlate: number | null,
): Set<number> {
  if (selectedPlate == null || !activeBlob) return new Set(checkedIds);
  return new Set(files.filter((f) => f.blob_hash === activeBlob).map((f) => f.id));
}

/** Part GLB url: the per-plate GLB when a plate is selected on this file's blob. */
export function partUrl(file: FileOut, plate: number | null): string {
  if (plate != null) {
    const match = selectablePlates(file.meta?.plates).find((p) => p.index === plate);
    if (match?.has_glb) return plateGlbUrl(file.blob_hash, plate);
  }
  return glbUrl(file);
}

// The blob the viewer is currently driving: plate cards only act on this one.
let activeBlob: string | null = null;

export function setActiveViewerBlob(blobHash: string | null) {
  if (activeBlob === blobHash) return;
  activeBlob = blobHash;
  for (const l of listeners) l();
}

export function useActiveViewerBlob(): string | null {
  return useSyncExternalStore(subscribe, () => activeBlob);
}

export function useIsActiveViewerBlob(blobHash: string): boolean {
  return useSyncExternalStore(subscribe, () => activeBlob === blobHash);
}

/** Forget a blob's selection (viewer unmounted / model changed). */
export function clearSelectedPlate(blobHash: string) {
  if (selected.delete(blobHash)) for (const l of listeners) l();
}

const selected = new Map<string, number | null>();
const listeners = new Set<() => void>();

export function getSelectedPlate(blobHash: string | null | undefined): number | null {
  return (blobHash && selected.get(blobHash)) || null;
}

export function setSelectedPlate(blobHash: string, plate: number | null) {
  if ((selected.get(blobHash) ?? null) === plate) return;
  selected.set(blobHash, plate);
  for (const l of listeners) l();
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => void listeners.delete(l);
}

/** Selected plate (1-based) for a blob; `null` = all plates. */
export function useSelectedPlate(blobHash: string | null | undefined): number | null {
  return useSyncExternalStore(subscribe, () => getSelectedPlate(blobHash));
}
