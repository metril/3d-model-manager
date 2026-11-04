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

/** The blob + plates the viewer's selector drives: the first CHECKED (shown)
 * file with 2+ plates, else the first such file. */
export function viewerPlates(
  files: FileOut[],
  checkedIds?: ReadonlySet<number>,
): { blobHash: string; plates: PlateOut[] } | null {
  const ordered = checkedIds ? [...files.filter((f) => checkedIds.has(f.id)), ...files.filter((f) => !checkedIds.has(f.id))] : files;
  for (const file of ordered) {
    const plates = selectablePlates(file.meta?.plates);
    if (plates.length > 0 && plates.some((p) => p.has_glb)) return { blobHash: file.blob_hash, plates };
  }
  return null;
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
