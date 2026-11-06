/**
 * Pure plate helpers + two tiny module-level stores shared by the viewer's
 * PlateSelector, the model-detail ProfilesPanel/PlatePanel and the print/slice
 * pickers: the viewer's active profile blob (`useActiveViewerBlob`) and the
 * selected plate per blob (`useSelectedPlate`). No React-three here (viewer
 * math lives in pure modules), and no prop plumbing between those components.
 */
import { useSyncExternalStore } from "react";

import { GLB_CACHE_V, glbUrl } from "@/components/viewer/viewable";
import { humanizeDuration } from "@/lib/format";
import { plainText } from "@/lib/plainText";
import type { FileOut, PlateOut, SourceMeta } from "@/api/types";

/** Plates worth offering a choice between: a selector needs at least two. */
export function selectablePlates(plates: PlateOut[] | null | undefined): PlateOut[] {
  return plates && plates.length >= 2 ? plates : [];
}

export const plateGlbUrl = (blobHash: string, plate: number) => `/api/blobs/${blobHash}/plates/${plate}/glb?v=${GLB_CACHE_V}`;
export const plateThumbUrl = (blobHash: string, plate: number) => `/api/blobs/${blobHash}/plates/${plate}/thumb`;

export type TFn = (k: string, o?: Record<string, unknown>) => string;

/** "Plate 2 · Lid" (name omitted when empty or just the default "Plate N");
 * with `total`, "Plate 2/4 · Lid". The one label shared by chips, tiles, pickers. */
export function plateLabel(plate: Pick<PlateOut, "index" | "name">, t: TFn, total?: number): string {
  const base = total != null ? t("profiles.plateOf", { index: plate.index, total }) : t("profiles.plateN", { index: plate.index });
  const name = plate.name?.trim();
  return name && name.toLowerCase() !== `plate ${plate.index}` ? `${base} · ${name}` : base;
}

/** A seeded plate only applies to the file it was picked on; once the dialog
 * resolves a different file (or none yet matches), it must not leak across. */
export function plateForFile(plate: number | null | undefined, plateFileId: number | null | undefined, fileId: number | null): number | null {
  if (!plate) return null;
  if (plateFileId == null) return plate;
  return fileId == null || fileId === plateFileId ? plate : null;
}

/** Highest plate index (chips say "2/4" even when plate numbers have gaps). */
export const plateTotal = (plates: PlateOut[]): number => plates.reduce((m, p) => Math.max(m, p.index), 0);

/** A colour safe to put in a `style` (hex only); anything else is dropped. */
export function safeColour(c: string | null | undefined): string | undefined {
  return c && /^#[0-9a-f]{6,8}$/i.test(c) ? c : undefined;
}

/** Fill a plate's missing time/weight from MakerWorld `source_meta.plates` (matched by index). */
export function withSourceFallback(plates: PlateOut[], source: SourceMeta | null | undefined): PlateOut[] {
  const sp = source?.plates;
  if (!sp || sp.length === 0) return plates;
  const out = plates.map((p) => {
    if (p.prediction_s != null && p.weight_g != null) return p;
    const m = sp.find((x) => x.index === p.index);
    const prediction_s = p.prediction_s ?? m?.prediction ?? null;
    const weight_g = p.weight_g ?? m?.weight ?? null;
    return prediction_s === p.prediction_s && weight_g === p.weight_g ? p : { ...p, prediction_s, weight_g };
  });
  return out.every((p, i) => p === plates[i]) ? plates : out;
}

/** Sum of known plate times/weights (null when no plate reports one). */
export function plateTotals(plates: PlateOut[]): { seconds: number | null; grams: number | null } {
  const sum = (k: "prediction_s" | "weight_g") => {
    const v = plates.map((p) => p[k]).filter((x): x is number => x != null);
    return v.length ? v.reduce((a, b) => a + b, 0) : null;
  };
  return { seconds: sum("prediction_s"), grams: sum("weight_g") };
}

/** Next/previous plate in the chip order [All, 1, 2, ...] with wrap-free clamping; null = All. */
export function stepPlate(plates: PlateOut[], value: number | null, dir: 1 | -1): number | null {
  const order: (number | null)[] = [null, ...plates.map((p) => p.index)];
  const i = Math.max(0, order.indexOf(value));
  return order[Math.min(order.length - 1, Math.max(0, i + dir))];
}

/** Next profile blob in dropdown order (wraps), including the All-files entry when offered. */
export function nextProfile(files: ViewerPlateFile[], hasAllFiles: boolean, current: string): string | null {
  const order = [...files.map((f) => f.blobHash), ...(hasAllFiles ? [ALL_FILES] : [])];
  if (order.length < 2) return null;
  return order[(Math.max(0, order.indexOf(current)) + 1) % order.length];
}

export interface PlateNav {
  blobHash: string;
  plates: PlateOut[];
  files?: ViewerPlateFile[];
  hasAllFiles?: boolean;
  value: number | null;
  onChange: (plate: number | null) => void;
  onFileChange?: (blobHash: string) => void;
}

/** Viewer stage plate/profile shortcuts: `[` previous, `]` next, `0` all plates, `P` cycle
 * profiles. Returns true when the key was handled. */
export function handlePlateKey(key: string, nav: PlateNav | null | undefined): boolean {
  if (!nav) return false;
  const list = selectablePlates(nav.plates);
  if (key === "[" || key === "]") {
    if (list.length === 0) return false;
    nav.onChange(stepPlate(list, nav.value, key === "]" ? 1 : -1));
    return true;
  }
  if (key === "0") {
    if (list.length === 0) return false;
    nav.onChange(null);
    return true;
  }
  if (key === "p" || key === "P") {
    const next = nav.files && nav.onFileChange ? nextProfile(nav.files, !!nav.hasAllFiles, nav.blobHash) : null;
    if (!next) return false;
    nav.onFileChange!(next);
    return true;
  }
  return false;
}

const warmed = new Set<string>();
/** Warm the HTTP cache for a plate GLB (chip hover/focus) so the swap is instant. */
export function preloadPlateGlb(blobHash: string, plate: number) {
  const url = plateGlbUrl(blobHash, plate);
  if (warmed.has(url) || typeof fetch !== "function") return;
  warmed.add(url);
  fetch(url).catch(() => warmed.delete(url));
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
  const seen = new Set<string>();
  for (const file of ordered) {
    if (seen.has(file.blob_hash)) continue;
    seen.add(file.blob_hash);
    out.push({ fileId: file.id, blobHash: file.blob_hash, label: profileTitle(file), plates: withSourceFallback(file.meta?.plates ?? [], file.source_meta) });
  }
  return out;
}

/** The entry the selector currently drives: the preferred blob if still offered, else the first. */
export function resolveActive(entries: ViewerPlateFile[], preferred: string | null): ViewerPlateFile | null {
  return entries.find((e) => e.blobHash === preferred) ?? entries[0] ?? null;
}

/** File ids to render. A profile (active blob) always isolates ONE file (`activeFileId`,
 * else every file of that blob); no active blob ("All files") keeps the normal
 * multi-file checked view. */
export function visibleParts(
  files: Pick<FileOut, "id" | "blob_hash">[],
  checkedIds: ReadonlySet<number>,
  activeBlob: string | null,
  activeFileId?: number | null,
): Set<number> {
  if (!activeBlob) return new Set(checkedIds);
  return new Set(files.filter((f) => f.blob_hash === activeBlob && (activeFileId == null || f.id === activeFileId)).map((f) => f.id));
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
export function plateTitle(plate: PlateOut, t: TFn): string | undefined {
  const parts: string[] = [];
  if (plate.fits === false) parts.push(t("profiles.tooFar"));
  const f = plate.filaments.map((x) => x.type ?? "").filter(Boolean);
  if (f.length) parts.push(f.join(" / "));
  if (plate.prediction_s != null) parts.push(humanizeDuration(plate.prediction_s));
  if (plate.weight_g != null) parts.push(`${Math.round(plate.weight_g)} g`);
  for (const c of plate.colour_changes ?? []) parts.push(t("profiles.colourChangeTo", { z: c.z.toFixed(1), colour: c.colour }));
  for (const p of plate.pauses ?? []) parts.push(t("profiles.pauseAt", { z: p.z.toFixed(1) }));
  return parts.length ? parts.join(" · ") : undefined;
}

/** Unsliced 3MF files shown as print profiles: only when at least one has plates. */
export function profileFiles(files: FileOut[]): FileOut[] {
  const list = files.filter((f) => f.format === "3mf" && f.kind !== "sliced");
  return list.some((f) => (f.meta?.plates?.length ?? 0) > 0) ? list : [];
}
