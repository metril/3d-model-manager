/**
 * Owns all the state the viewer stage renders from -- checked parts, per-part
 * colors, background, lighting, the printer id, and the pop-out action --
 * lifted out of the model-detail tab so the inline tab, the Expand dialog,
 * and the standalone pop-out window all share ONE implementation. The tab
 * calls it with no `initial`, so it behaves exactly as before; the window
 * seeds it from its URL. Returns a ready-to-spread `stageProps` bundle (the
 * caller only adds the per-surface `variant`/`showExpand`/`showWindowButtons`
 * flags), keeping the assembly in one place instead of duplicated per caller.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";

import { modelQueryOptions, uploadModelCover } from "@/api/library";
import { usePrinters } from "@/api/printers";
import { useViewerBackground, type BackgroundPreset } from "@/components/viewer/background";
import { useViewerLighting, type LightingPreset } from "@/components/viewer/lighting";
import {
  encodePartColors,
  loadPartColors,
  savePartColors,
  type PartColors,
} from "@/components/viewer/partColors";
import { applyToParts } from "@/components/viewer/quickColors";
import {
  useViewerTools,
  type SceneStats,
  type ViewerApi,
  type ViewerToolsState,
} from "@/components/viewer/tools";
import type { ViewerStageProps } from "@/components/viewer/ViewerStage";
import {
  buildPlateLayout,
  clearSelectedPlate,
  layoutPartId,
  partUrl,
  plateGlbUrl,
  resolveActive,
  setActiveViewerBlob,
  setSelectedPlate,
  useActiveViewerBlob,
  useSelectedPlate,
  viewerPlates,
  visibleParts,
} from "@/components/viewer/plates";
import type { ViewerPart } from "@/components/viewer/viewable";
import { glbUrl } from "@/components/viewer/viewable";
import type { FileOut } from "@/api/types";

// Fallback build-plate mm side length when there's no configured printer
// (or it has no `build_volume_mm`) to derive one from.
const PLATE_SIZE_MM = 256;

/** Seed values so the pop-out window can start from its URL instead of this
 * tab's localStorage. Every field is optional; absent means "use the tab's
 * normal default" (first part checked, persisted colors/background/lighting,
 * panel open). */
export interface ViewerSceneInitial {
  checkedIds?: number[];
  colors?: PartColors;
  background?: { preset: BackgroundPreset; custom?: string };
  lighting?: LightingPreset;
  tools?: Partial<ViewerToolsState>;
}

type StagePropsBundle = Omit<
  ViewerStageProps,
  "variant" | "showExpand" | "onExpand" | "showWindowButtons"
>;

export function useViewerScene({
  slug,
  files,
  initial,
  persist = true,
  coverUrl,
  defaultAllChecked = false,
}: {
  slug: string;
  files: FileOut[];
  initial?: ViewerSceneInitial;
  /** R9-D item 8: the model's cover thumbnail URL, threaded straight through
   * to `ViewerStage`'s load crossfade -- see that prop's doc comment.
   * Undefined for callers with no cover to show (the pop-out window). */
  coverUrl?: string | null;
  /** Whether this surface writes its state back to shared storage. The tab
   * persists (colors per-model, background/lighting globally). The pop-out
   * window is a URL-derived VIEW and passes `false`: it's seeded with a SUBSET
   * of colors (writing that back would drop the rest of the model's colors),
   * and it reads its background/lighting from the URL, so persisting either
   * would let comparing settings in a pop-out silently change the tab's saved
   * defaults. */
  persist?: boolean;
  /** When `initial.checkedIds` is absent, seed with every file checked
   * instead of only `files[0]`. The studio surface passes `true` -- its
   * `FileRail` is the single source of truth for part visibility and always
   * shows the "N of M" count plus All/None, so a model shouldn't open with
   * only one of N parts visible. `/viewer/$slug` (no option passed) keeps the
   * original single-part default. */
  defaultAllChecked?: boolean;
}): { stageProps: StagePropsBundle } {
  const [checkedIds, setCheckedIds] = useState<ReadonlySet<number>>(
    () =>
      new Set(
        initial?.checkedIds ?? (defaultAllChecked ? files.map((file) => file.id) : files[0] ? [files[0].id] : []),
      ),
  );
  const [colors, setColors] = useState<PartColors>(() => initial?.colors ?? loadPartColors(slug));
  const { preset, custom, color, setPreset, setCustom } = useViewerBackground(
    initial?.background,
    persist,
  );
  const { preset: lightingPreset, rig: lighting, setPreset: setLighting } = useViewerLighting(
    initial?.lighting,
    persist,
  );
  const { tools, setTools } = useViewerTools(initial?.tools, persist);
  const [stats, setStats] = useState<SceneStats | null>(null);
  // `fitSignal` is a counter, not a boolean -- "Fit view" is a one-shot
  // action, not a state, and `ModelViewer`'s `BoundsRefitter` refits on
  // CHANGE (a `useLayoutEffect` dep), so two fits in a row (e.g. pressing `F`
  // twice) each need to register as a distinct change rather than
  // coalescing into a no-op. Also bumped by the ortho toggle's post-swap
  // recovery -- see `ViewerStage.tsx`'s comment on that handler.
  const [fitSignal, setFitSignal] = useState(0);
  // The imperative surface `ModelViewer` publishes (today: `screenshot`) --
  // there's no prop path from a DOM button click into a `<Canvas>` child, so
  // this ref is the bridge (see `scene/helpers.tsx`'s `CaptureBridge`).
  const viewerApiRef = useRef<ViewerApi | null>(null);
  const printers = usePrinters();
  const printerId = printers.data?.[0]?.id;
  // `ModelViewer`'s plate grid is square, so only X is used for the
  // footprint side length -- matches the previous hardcoded behavior, now
  // sourced from the first printer's build volume when one is configured.
  const plateSize = printers.data?.[0]?.build_volume_mm?.x ?? PLATE_SIZE_MM;
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!persist) return;
    savePartColors(slug, colors);
  }, [slug, colors, persist]);

  // R13a Cover action (risk resolution 6): captures the canvas and uploads
  // it through the normal ingest pipeline. The new thumbnail derivative
  // (`render_thumb`) only exists once the async PNG pipeline step finishes,
  // so this relies on the model-detail query invalidation below to pick up
  // the new `cover_blob_hash` plus the success toast to confirm the action
  // -- no optimistic local preview.
  const [capturingCover, setCapturingCover] = useState(false);

  // Disabled entirely when `persist` is false (the pop-out window, see this
  // hook's `persist` doc comment) -- there's no model-detail card to reflect
  // the new cover there, and the endpoint is keyed by `slug` the same way
  // every other persisted write here is.
  const captureCover = useCallback(async () => {
    if (!persist) return;
    setCapturingCover(true);
    try {
      const blob = await viewerApiRef.current?.screenshot();
      if (!blob) {
        toast.error("Couldn't capture a screenshot");
        return;
      }

      await uploadModelCover(slug, blob);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: modelQueryOptions(slug).queryKey }),
        queryClient.invalidateQueries({ queryKey: ["models", "list"] }),
      ]);
      toast.success("Cover updated");
    } catch {
      toast.error("Couldn't update cover");
    } finally {
      setCapturingCover(false);
    }
  }, [persist, slug, queryClient]);

  // B1 "toggle-fix core": every combinable file becomes a part, checked or
  // not -- the scene keeps them all mounted and toggles `visible` instead of
  // the checklist changing which parts even exist in the tree (which used to
  // force the whole canvas to remount on every checkbox click). `checkedIds`
  // still drives `visible`, `checkedList`, and the "N of M" header below.
  const plateFiles = useMemo(() => viewerPlates(files, checkedIds), [files, checkedIds]);
  const preferredBlob = useActiveViewerBlob();
  const plateInfo = useMemo(() => resolveActive(plateFiles, preferredBlob), [plateFiles, preferredBlob]);
  const plateBlob = plateInfo?.blobHash ?? null;
  // Publish which blob this viewer drives (plate cards act only on it) and
  // forget its selection when the viewer goes away, the model changes or the
  // file picker moves on.
  useEffect(() => {
    if (!plateBlob) return;
    setActiveViewerBlob(plateBlob);
    return () => {
      setActiveViewerBlob(null);
      clearSelectedPlate(plateBlob);
    };
  }, [plateBlob]);
  const selectedPlate = useSelectedPlate(plateBlob);
  // A selected plate isolates that file; "All plates" restores the checked set.
  const shown = useMemo(() => visibleParts(files, checkedIds, plateBlob, selectedPlate), [files, checkedIds, plateBlob, selectedPlate]);
  // Isolation DROPS the other files from the tree rather than hiding them:
  // `Resize`/`Center` measure hidden parts too (so checkbox toggles never
  // shift the layout), which would ground and centre the lone plate against
  // the whole multi-file pile.
  const isolating = selectedPlate != null && plateBlob != null;
  // Real-bed layout (plates carry `bed`/`origin`): the active blob's file(s)
  // become one part per plate (All plates) or the lone plate, placed on its
  // bed. Other files are dropped in BOTH modes -- they have no place on a
  // bed and would distort the layout framing (same rule as isolation).
  const layout = useMemo(
    () => (plateInfo && plateBlob ? buildPlateLayout(plateInfo.plates, selectedPlate) : null),
    [plateInfo, plateBlob, selectedPlate],
  );
  const parts: ViewerPart[] = useMemo(() => {
    if (layout) {
      return files
        .filter((file) => file.blob_hash === plateBlob)
        .flatMap((file) =>
          layout.parts.map((lp) => ({
            id: layoutPartId(file.id, lp.plate),
            url: lp.plate == null ? glbUrl(file) : plateGlbUrl(file.blob_hash, lp.plate),
            color: colors[file.id],
            visible: shown.has(file.id),
            offset: lp.offset,
          })),
        );
    }
    return (isolating ? files.filter((file) => shown.has(file.id)) : files).map((file) => ({
      id: file.id,
      // Per-plate GLB when a plate is selected on this file's blob.
      url: partUrl(file, plateBlob === file.blob_hash ? selectedPlate : null),
      color: colors[file.id],
      visible: shown.has(file.id),
    }));
  }, [files, shown, colors, plateBlob, selectedPlate, isolating, layout]);

  // A plate swap loads different geometry (and isolates one file): reset
  // explode (its offsets describe the old parts) and reframe/fit to the bed.
  const lastPlate = useRef(`${plateBlob}:${selectedPlate}`);
  const explodeNow = tools.explode;
  useEffect(() => {
    const key = `${plateBlob}:${selectedPlate}`;
    if (lastPlate.current === key) return;
    lastPlate.current = key;
    if (explodeNow !== 0) setTools({ explode: 0 });
    setFitSignal((prev) => prev + 1);
  }, [plateBlob, selectedPlate, explodeNow, setTools]);

  function toggleFile(fileId: number, checked: boolean) {
    setCheckedIds((prev) => {
      const next = new Set(prev);
      if (checked) {
        next.add(fileId);
      } else {
        next.delete(fileId);
      }
      return next;
    });
  }

  // Parts All/None (panel header buttons): flips every file's `visible` flag
  // in one step, the same way a single checkbox click flips one -- `parts`
  // stays permanently mounted (B1 "toggle-fix core"), so this never remounts
  // the scene, it only changes which parts the "N of M" count and `visible`
  // flags cover.
  function setAllChecked(checked: boolean) {
    setCheckedIds(checked ? new Set(files.map((file) => file.id)) : new Set());
  }

  function setPartColor(fileId: number, hex: string) {
    setColors((prev) => ({ ...prev, [fileId]: hex }));
  }

  function clearPartColor(fileId: number) {
    setColors((prev) => {
      const next = { ...prev };
      delete next[fileId];
      return next;
    });
  }

  // R13a dock quick swatches (Key decision 2): bulk-writes one hex into
  // every CHECKED part's entry of the same `colors` map a per-part override
  // writes one key of -- same `setColors`/persistence path, just applied to
  // `checkedIds` instead of a single file id.
  function setAllColors(hex: string) {
    setColors((prev) => applyToParts(prev, [...checkedIds], hex));
  }

  const checkedList = [...checkedIds];
  const hasColors = Object.keys(colors).length > 0;

  // Open the current selection (or a single part) in its OWN browser window
  // (M8 G1). The window re-derives everything from the URL so it matches what's
  // shown here at open time. `bg` carries the background PRESET (not a resolved
  // hex) so the window's Background control lands on the right segment; `bgc`
  // carries the custom hex only when that preset is "custom"; `light` carries
  // the lighting preset. Multiple windows are cheap -- GLB urls are
  // content-addressed.
  //
  // Task 6 adds the view-tools params (`grid`/`wf`/`rot`/`cam`/`sec`/`ex`),
  // mirroring `tools` at the moment the window opens. Every one of them is
  // OMITTED when it's already at `DEFAULT_TOOLS` -- a shorter URL for the
  // (overwhelmingly common) case where nothing but the background/lighting
  // was touched -- EXCEPT `grid`, which is always written explicitly. `grid`
  // defaults to `true`, and the receiving window's `useViewerTools` falls
  // back to reading the OPENER's `viewer-tools` localStorage entry when its
  // `initial.grid` is absent (see that hook's doc comment) -- omitting it
  // here would make the window's grid state depend on implicit, possibly
  // stale shared storage instead of the exact toggle this tab is showing
  // right now, which is the one thing "mirror the opener" can't leave to
  // chance.
  function openInWindow(ids: number[]) {
    const params = new URLSearchParams({ ids: ids.join(","), bg: preset, light: lightingPreset });
    if (preset === "custom") params.set("bgc", custom);
    const subset: PartColors = {};
    for (const id of ids) if (colors[id]) subset[id] = colors[id];
    const encoded = encodePartColors(subset);
    if (encoded) params.set("colors", encoded);

    params.set("grid", tools.grid ? "1" : "0");
    if (tools.shading === "wireframe") params.set("wf", "1");
    if (tools.shading === "xray") params.set("xr", "1");
    if (tools.autoRotate) params.set("rot", "1");
    if (tools.ortho) params.set("cam", "o");
    if (tools.section.enabled) {
      params.set("sec", `${tools.section.axis}:${tools.section.t.toFixed(2)}`);
    }
    if (tools.explode !== 0) params.set("ex", tools.explode.toFixed(2));

    window.open(`/viewer/${slug}?${params.toString()}`, "_blank", "popup=1,width=1024,height=768,noopener");
  }

  const stageProps: StagePropsBundle = {
    slug,
    files,
    checkedIds,
    onToggleFile: toggleFile,
    onSetAllChecked: setAllChecked,
    colors,
    onSetPartColor: setPartColor,
    onClearPartColor: clearPartColor,
    onSetAllColors: setAllColors,
    hasColors,
    onResetColors: () => setColors({}),
    preset,
    custom,
    background: color,
    onPresetChange: setPreset,
    onCustomChange: setCustom,
    lightingPreset,
    lighting,
    onLightingChange: setLighting,
    printerId,
    onApplyAmsColors: (map) => setColors((prev) => ({ ...prev, ...map })),
    parts,
    checkedList,
    onOpenWindow: openInWindow,
    tools,
    onToolsChange: setTools,
    stats,
    onStats: setStats,
    plateSize,
    layout,
    fitSignal,
    onFit: () => setFitSignal((prev) => prev + 1),
    viewerApiRef,
    coverUrl,
    onCaptureCover: captureCover,
    capturingCover,
    canCaptureCover: persist,
    plateInfo: plateInfo
      ? {
          blobHash: plateInfo.blobHash,
          plates: plateInfo.plates,
          files: plateFiles,
          value: selectedPlate,
          onChange: (plate) => setSelectedPlate(plateInfo.blobHash, plate),
          onFileChange: (blobHash) => {
            clearSelectedPlate(plateInfo.blobHash);
            setActiveViewerBlob(blobHash);
          },
        }
      : null,
  };

  return { stageProps };
}
