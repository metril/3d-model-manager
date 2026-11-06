/**
 * Pure plate helpers + a tiny selection store shared by the viewer's
 * PlateSelector, the model-detail PlatePanel and the print/slice pickers.
 * No React-three here (viewer math lives in pure modules); the store is a
 * module-level map keyed by blob hash so unrelated components agree on the
 * selected plate without prop plumbing.
 */
import { useSyncExternalStore } from "react";

import { GLB_CACHE_V, glbUrl } from "@/components/viewer/viewable";
import { plainText } from "@/lib/plainText";
import type { FileOut, PlateOut } from "@/api/types";

/** Plates worth offering a choice between: a selector needs at least two. */
export function selectablePlates(plates: PlateOut[] | null | undefined): PlateOut[] {
  return plates && plates.length >= 2 ? plates : [];
}

export const plateGlbUrl = (blobHash: string, plate: number) => `/api/blobs/${blobHash}/plates/${plate}/glb?v=${GLB_CACHE_V}`;
export const plateThumbUrl = (blobHash: string, plate: number) => `/api/blobs/${blobHash}/plates/${plate}/thumb`;

/** "Plate 2 · Lid" (name omitted when empty or just the default "Plate N"). */
export function plateLabel(plate: Pick<PlateOut, "index" | "name">): string {
  const base = `Plate ${plate.index}`;
  const name = plate.name?.trim();
  return name && name.toLowerCase() !== base.toLowerCase() ? `${base} · ${name}` : base;
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

/** Sentinel "active blob": the combined view of every file (profiles + other meshes). */
export const ALL_FILES = "*";

/** Profile display title: the plain-text source title, else the file label. */
export function profileTitle(file: Pick<FileOut, "rel_path" | "source_meta">): string {
  return plainText(file.source_meta?.title) || fileLabel(file.rel_path ?? "");
}

/** Every print profile (unsliced 3MF) the viewer can show, single-plate ones
 * included. CHECKED (shown) files first, then the rest; one entry per blob. */
export function viewerPlates(files: FileOut[], checkedIds?: ReadonlySet<number>): ViewerPlateFile[] {
  const profiles = profileFiles(files);
  const ordered = checkedIds ? [...profiles.filter((f) => checkedIds.has(f.id)), ...profiles.filter((f) => !checkedIds.has(f.id))] : profiles;
  const out: ViewerPlateFile[] = [];
  for (const file of ordered) {
    if (out.some((o) => o.blobHash === file.blob_hash)) continue;
    out.push({ fileId: file.id, blobHash: file.blob_hash, label: profileTitle(file), plates: file.meta?.plates ?? [] });
  }
  return out;
}

/** The entry the selector currently drives: the preferred blob if still offered, else the first. */
export function resolveActive(entries: ViewerPlateFile[], preferred: string | null): ViewerPlateFile | null {
  return entries.find((e) => e.blobHash === preferred) ?? entries[0] ?? null;
}

/** File ids to render. A profile (active blob) always isolates its file(s);
 * no active blob ("All files") keeps the normal multi-file checked view. */
export function visibleParts(
  files: Pick<FileOut, "id" | "blob_hash">[],
  checkedIds: ReadonlySet<number>,
  activeBlob: string | null,
): Set<number> {
  if (!activeBlob) return new Set(checkedIds);
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

/** One printer bed in the plate layout, in viewer space (Y-up, mm): `offset`
 * is the bed's corner; the bed covers x in [ox, ox+bx], z in [oz-by, oz]. */
export interface PlateBed {
  index: number;
  name: string;
  size: [number, number];
  offset: [number, number, number];
}

/** A part of the layout: a plate GLB (`plate`) or the full GLB (`null`). */
export interface PlateLayoutPart {
  plate: number | null;
  offset: [number, number, number];
}

export interface PlateLayout {
  beds: PlateBed[];
  parts: PlateLayoutPart[];
}

/** Z-up plate origin [ox, oy] (backend `plate_origin`) -> Y-up offset. */
export const originToOffset = (origin: number[]): [number, number, number] => [origin[0], 0, 0 - origin[1]];

const hasBed = (p: PlateOut) => p.bed?.length === 2 && p.origin?.length === 2;

/** Real-bed layout for a plated file, or null (legacy rows without `bed`,
 * or fewer than two plates) so the viewer keeps its single square grid.
 * Selected plate: that bed alone, its GLB bed-local (offset 0) -- or, with no
 * per-plate GLB, the full GLB at raw world coords and the bed at its origin.
 * All plates: every bed at its origin; each plate GLB offset by it, or the
 * full GLB (offset 0) when no plate has one. */
export function buildPlateLayout(plates: PlateOut[], selected: number | null): PlateLayout | null {
  if (plates.length < 2 || !plates.every(hasBed)) return null;
  const bedOf = (p: PlateOut): PlateBed => ({
    index: p.index,
    name: p.name ?? "",
    size: [p.bed![0], p.bed![1]],
    offset: originToOffset(p.origin!),
  });
  const one = selected != null ? plates.find((p) => p.index === selected) : undefined;
  if (one) {
    const bed = bedOf(one);
    if (one.has_glb) return { beds: [{ ...bed, offset: [0, 0, 0] }], parts: [{ plate: one.index, offset: [0, 0, 0] }] };
    return { beds: [bed], parts: [{ plate: null, offset: [0, 0, 0] }] };
  }
  const beds = plates.map(bedOf);
  const withGlb = plates.filter((p) => p.has_glb);
  const parts: PlateLayoutPart[] = withGlb.length
    ? withGlb.map((p) => ({ plate: p.index, offset: originToOffset(p.origin!) }))
    : [{ plate: null, offset: [0, 0, 0] }];
  return { beds, parts };
}

/** Bounding box of the beds (y = 0 plane) as [min, max], or null if empty. */
export function bedsBounds(beds: PlateBed[]): { min: [number, number, number]; max: [number, number, number] } | null {
  if (beds.length === 0) return null;
  const min: [number, number, number] = [Infinity, 0, Infinity];
  const max: [number, number, number] = [-Infinity, 0, -Infinity];
  for (const b of beds) {
    min[0] = Math.min(min[0], b.offset[0]);
    max[0] = Math.max(max[0], b.offset[0] + b.size[0]);
    min[2] = Math.min(min[2], b.offset[2] - b.size[1]);
    max[2] = Math.max(max[2], b.offset[2]);
  }
  return { min, max };
}

/** Stable id for a plate part: the file's own id for the full GLB, a negative
 * synthetic one per plate (cannot collide with DB ids). */
export const layoutPartId = (fileId: number, plate: number | null): number =>
  plate == null ? fileId : -(fileId * 1000 + plate);

// The blob the viewer is currently driving: plate cards only act on this one.
let activeBlob: string | null = null;

export function setActiveViewerBlob(blobHash: string | null) {
  if (activeBlob === blobHash) return;
  activeBlob = blobHash;
  for (const l of listeners) l();
}

export const getActiveViewerBlob = () => activeBlob;

/** Switch the viewer to a profile (or ALL_FILES), dropping the previous profile's plate pick. */
export function selectProfile(blobHash: string) {
  if (activeBlob === blobHash) return;
  if (activeBlob && activeBlob !== ALL_FILES) clearSelectedPlate(activeBlob);
  setActiveViewerBlob(blobHash);
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

/** Plain-text tooltip for a plate chip. */
export function plateTitle(plate: PlateOut, t: (k: string, o?: Record<string, unknown>) => string): string | undefined {
  const parts: string[] = [];
  if (plate.fits === false) parts.push(t("profiles.tooFar"));
  const f = plate.filaments.map((x) => x.type ?? "").filter(Boolean);
  if (f.length) parts.push(f.join(" / "));
  for (const c of plate.colour_changes ?? []) parts.push(t("profiles.colourChange", { z: c.z.toFixed(1) }) + ` → ${c.colour}`);
  for (const p of plate.pauses ?? []) parts.push(t("profiles.pauseAt", { z: p.z.toFixed(1) }));
  return parts.length ? parts.join(" · ") : undefined;
}

/** Unsliced 3MF files shown as print profiles: only when at least one has plates. */
export function profileFiles(files: FileOut[]): FileOut[] {
  const list = files.filter((f) => f.format === "3mf" && f.kind !== "sliced");
  return list.some((f) => (f.meta?.plates?.length ?? 0) > 0) ? list : [];
}
