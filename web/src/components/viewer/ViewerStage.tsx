import {
  Component,
  Suspense,
  lazy,
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { LoaderCircleIcon } from "lucide-react";

import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useStageFullscreen } from "@/components/viewer/useStageFullscreen";
import { cn } from "@/lib/utils";
import type { BackgroundPreset } from "@/components/viewer/background";
import type { ExplodeMode } from "@/components/viewer/explode";
import type { LightingPreset, LightingRig } from "@/components/viewer/lighting";
import type { PartColors } from "@/components/viewer/partColors";
import {
  type SceneStats,
  type ViewerApi,
  type ViewerToolsState,
} from "@/components/viewer/tools";
import { ViewerDock } from "@/components/viewer/ViewerDock";
import { ViewerFooterStrip } from "@/components/viewer/ViewerFooterStrip";
import { ViewerMorePanel } from "@/components/viewer/ViewerMorePanel";
import { ViewerTopOverlay } from "@/components/viewer/ViewerTopOverlay";
import type { ViewerPart } from "@/components/viewer/viewable";
import type { FileOut } from "@/api/types";

// three.js/@react-three/fiber/drei are heavy (Global Constraints "BUNDLE
// RULE") — load them only once a GLB actually needs rendering, so the main
// bundle never pays for the viewer on pages that don't render this stage.
const ModelViewer = lazy(() => import("@/components/viewer/ModelViewer"));

/** A centered card used for every "nothing to render here" state -- shared by
 * this stage's empty-selection case and `ViewerTab`'s file-status cards
 * (pending / failed / unsupported / plain gcode), so it's exported rather
 * than duplicated. */
export function PlaceholderCard({
  title,
  description,
  destructive = false,
}: {
  title: string;
  description: string;
  destructive?: boolean;
}) {
  return (
    <Card className="mx-auto mt-8 max-w-md">
      <CardHeader className="items-center text-center">
        <CardTitle className={destructive ? "text-destructive" : undefined}>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
    </Card>
  );
}

// React has no hook form for error boundaries (`componentDidCatch`/
// `getDerivedStateFromError` are class-only APIs), and this project has no
// `react-error-boundary` dependency or existing boundary pattern to reuse
// -- a tiny local class is the documented fallback (M2-Minor 1). Without
// this, a GLB fetch/parse throw from the lazy R3F viewer propagates past
// this tab to the router's top-level error surface, taking down the whole
// model-detail page instead of just this one preview.
interface ViewerErrorBoundaryProps {
  children: ReactNode;
  /** B1 "toggle-fix core": used to be a `key` on this boundary (the joined
   * checked ids), which cleared a stuck error by remounting the boundary --
   * and everything under it, including the `<Canvas>` -- on every checkbox
   * toggle, not just after a crash. Now it's a plain prop: parts stay
   * mounted, and `getDerivedStateFromProps` below clears `hasError` itself
   * the next time the checked selection actually changes (comparing against
   * the resetKey the error happened under, tracked in state), so a crash
   * doesn't get stuck on the fallback forever without remounting anything. */
  resetKey: string;
  /** Fix wave finding 3: lets `ViewerStage` know the canvas crashed so it can
   * drop the thumbnail crossfade cover -- otherwise a load failure left the
   * cover image sitting opaque over this boundary's own fallback forever
   * (the cover only ever cleared on `onPartLoaded`, which a crash never
   * fires). */
  onError?: () => void;
}

interface ViewerErrorBoundaryState {
  hasError: boolean;
  resetKey: string;
}

class ViewerErrorBoundary extends Component<ViewerErrorBoundaryProps, ViewerErrorBoundaryState> {
  state: ViewerErrorBoundaryState = { hasError: false, resetKey: this.props.resetKey };

  static getDerivedStateFromError(): Pick<ViewerErrorBoundaryState, "hasError"> {
    return { hasError: true };
  }

  componentDidCatch() {
    this.props.onError?.();
  }

  // Derives state from props instead of a `componentDidUpdate` + `setState`
  // pair (oxlint's `react/no-did-update-set-state` flags that combination as
  // update-thrashing-prone) -- functionally the same reset, just expressed
  // as a pure props+state -> state mapping: whenever `resetKey` changes,
  // clear `hasError` (a no-op if it was already false) and resync the
  // tracked key for the next comparison.
  static getDerivedStateFromProps(
    props: ViewerErrorBoundaryProps,
    state: ViewerErrorBoundaryState,
  ): ViewerErrorBoundaryState | null {
    if (props.resetKey === state.resetKey) return null;
    return { hasError: false, resetKey: props.resetKey };
  }

  render() {
    if (this.state.hasError) {
      return (
        <PlaceholderCard
          destructive
          title="Preview failed to load"
          description="The 3D preview crashed while loading this file. It may be corrupt or in an unsupported format."
        />
      );
    }
    return this.props.children;
  }
}

/** The combined multi-part canvas, guarded by its own error boundary. Reused
 * for the inline box, the pop-out dialog, and the standalone window (via
 * `ViewerStage`) so all three stay visually and behaviorally identical --
 * only the wrapping height/size differs.
 *
 * `parts` now always carries EVERY combinable file (B1 "toggle-fix core"),
 * checked or not -- `PlaceholderCard` here only covers the case where there
 * is nothing combinable AT ALL (no GLB-ready files exist), which in practice
 * `ViewerTab`/`ViewerWindowPage` already guard against before ever reaching
 * this component. When parts exist but none is checked, the canvas stays
 * mounted (unmounting it would blank the WebGL context, IBL bake, and camera
 * for no reason) and a `pointer-events-none` hint overlays it instead. The
 * error boundary's `resetKey` is the checked ids only -- switching the
 * selection clears a stuck crash without remounting the boundary (see its
 * comment above). */
function MeshCanvas({
  parts,
  background,
  lighting,
  tools,
  plateSize,
  onStats,
  fitSignal,
  apiRef,
  onPartLoaded,
  onExplodeModeChange,
  onCameraPresetClear,
  hasCoverThumbnail,
  onError,
}: {
  parts: ViewerPart[];
  background: string;
  lighting: LightingRig;
  tools: ViewerToolsState;
  plateSize: number;
  onStats: (stats: SceneStats | null) => void;
  fitSignal: number;
  apiRef: React.MutableRefObject<ViewerApi | null>;
  /** Task 5 explode view: forwarded straight through to `ModelViewer` -- see
   * `ViewerStage`'s `handlePartLoaded` for what it does. */
  onPartLoaded: () => void;
  onExplodeModeChange: (mode: ExplodeMode) => void;
  /** R10 camera presets: forwarded to `ModelViewer`'s `OrbitPresetGuard` --
   * fires on a real user orbit so `ViewerStage` can clear `tools.
   * cameraPreset` back to `null`. */
  onCameraPresetClear: () => void;
  /** Fix wave finding 3: forwarded to `ViewerErrorBoundary` so a canvas-level
   * crash can clear the thumbnail crossfade cover in `ViewerStage`. */
  onError?: () => void;
  /** R9-D item 8: when the model has a cover thumbnail, `ViewerStage`
   * already shows it as a full-stage overlay while the GLB loads (see
   * `ViewerStage`'s thumbnail layer), so this component's own Suspense
   * fallback (the lazy-chunk import only -- GLB loads happen inside
   * `ModelViewer`'s own internal Canvas suspense and never reach here) can
   * skip the full-stage `Skeleton` and render nothing; a small corner
   * spinner in `ViewerStage` covers the "still loading" affordance instead.
   * Falls back to the old full-stage `Skeleton` when there's no thumbnail to
   * cover the gap (e.g. the pop-out window, or a model with no cover
   * image). */
  hasCoverThumbnail: boolean;
}) {
  if (parts.length === 0) {
    return (
      <PlaceholderCard
        title="Select a part to preview"
        description="Select a part in the panel to render it."
      />
    );
  }

  const visibleParts = parts.filter((part) => part.visible);

  return (
    <>
      <ViewerErrorBoundary resetKey={visibleParts.map((part) => part.id).join("|")} onError={onError}>
        <Suspense fallback={hasCoverThumbnail ? null : <Skeleton className="h-full w-full" />}>
          <ModelViewer
            parts={parts}
            background={background}
            lighting={lighting}
            tools={tools}
            plateSize={plateSize}
            onStats={onStats}
            fitSignal={fitSignal}
            apiRef={apiRef}
            onPartLoaded={onPartLoaded}
            onExplodeModeChange={onExplodeModeChange}
            onCameraPresetClear={onCameraPresetClear}
          />
        </Suspense>
      </ViewerErrorBoundary>
      {visibleParts.length === 0 && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-background/50 text-sm text-muted-foreground">
          No parts selected
        </div>
      )}
      {/* "How big is this print?" chip is now `ViewerTopOverlay`'s dims pill
          -- this local stats overlay is gone (see that component). */}
    </>
  );
}

export interface ViewerStageProps {
  /** The model's slug -- used only as the downloaded screenshot's filename
   * (`${slug}.png`). */
  slug: string;
  files: FileOut[];
  checkedIds: ReadonlySet<number>;
  onToggleFile: (fileId: number, checked: boolean) => void;
  /** Parts popover All/None buttons -- flips every file's `visible` flag in
   * one step, the same way a single checkbox click flips one (see
   * `useViewerScene`'s `setAllChecked`). `parts` stays mounted either way. */
  onSetAllChecked: (checked: boolean) => void;
  colors: PartColors;
  onSetPartColor: (fileId: number, hex: string) => void;
  onClearPartColor: (fileId: number) => void;
  /** R13a dock quick swatches (Key decision 2): bulk-writes one hex into
   * every checked part's color. */
  onSetAllColors: (hex: string) => void;
  hasColors: boolean;
  onResetColors: () => void;
  preset: BackgroundPreset;
  custom: string;
  background: string;
  onPresetChange: (preset: BackgroundPreset) => void;
  onCustomChange: (custom: string) => void;
  lightingPreset: LightingPreset;
  lighting: LightingRig;
  onLightingChange: (preset: LightingPreset) => void;
  printerId: number | undefined;
  onApplyAmsColors: (colors: PartColors) => void;
  parts: ViewerPart[];
  checkedList: number[];
  onOpenWindow: (ids: number[]) => void;
  /** Shows the "New window" / "Parts in windows" pop-out buttons (now inside
   * `ViewerMorePanel`). False in the `window` variant -- you're already in a
   * pop-out, so re-popping is noise. */
  showWindowButtons: boolean;
  /** "inline" gets its own `h-[450px]` since it isn't inside a sized flex
   * ancestor; "window" must NOT hard-code a height -- the window page's
   * `h-svh` wrapper already provides it, and the stage just needs to flex to
   * fill it. */
  variant: "inline" | "window";
  /** View-affecting toggles (build-plate grid, auto-rotate, orthographic
   * camera, wireframe, cross-section, explode) -- see `tools.ts`. Driven by
   * `ViewerDock`/`ViewerMorePanel` and the `F`/`R`/`W`/`G` keyboard
   * shortcuts. */
  tools: ViewerToolsState;
  onToolsChange: (patch: Partial<ViewerToolsState>) => void;
  /** "How big is this print?" -- the combined mm bounding box + triangle
   * count of the currently visible, loaded parts, reported by `ModelViewer`
   * and rendered by `ViewerTopOverlay`'s dims pill. `null` until something
   * visible has loaded. */
  stats: SceneStats | null;
  onStats: (stats: SceneStats | null) => void;
  /** The build plate's mm side length -- single source in `useViewerScene`
   * today, so a future settings surface can override it in one place. */
  plateSize: number;
  /** A counter `ModelViewer`'s `BoundsRefitter` treats as "refit now" on
   * every change -- `useViewerScene`'s `onFit` bumps it. A counter rather
   * than a boolean since "fit" is a one-shot action: two fits in a row (the
   * `F` key pressed twice, or the ortho toggle's post-swap recovery
   * immediately after a manual fit) each need to register as a distinct
   * change, not coalesce into a no-op. */
  fitSignal: number;
  /** Bumps `fitSignal`. Wired to the "Fit view" button, the `F` key, and
   * (after `onToolsChange({ ortho: ... })`) the Orthographic toggle -- see
   * that handler's comment for why the ortho swap needs a follow-up fit. */
  onFit: () => void;
  /** The imperative surface `ModelViewer` publishes into (today:
   * `screenshot`) -- see `scene/helpers.tsx`'s `CaptureBridge`. There's no
   * prop path from the Screenshot button's click handler into a `<Canvas>`
   * child otherwise. */
  viewerApiRef: React.MutableRefObject<ViewerApi | null>;
  /** R9-D item 8: the model's cover/thumbnail image URL (`null`/`undefined`
   * when the model has none, e.g. the pop-out window today), rendered as a
   * full-stage overlay over the canvas until the first part has loaded, then
   * faded out over ~250ms (`prefers-reduced-motion` swaps instantly via
   * Tailwind's `motion-reduce:` variant -- no JS branching needed). Replaces
   * the old "blank canvas until the GLB pops in" gap with something to look
   * at. */
  coverUrl?: string | null;
  /** R13a Cover action (risk resolution 6): captures the canvas + uploads it
   * as the model's new cover. */
  onCaptureCover: () => void;
  capturingCover: boolean;
  /** False in the pop-out window (`persist=false` in `useViewerScene`) --
   * there's no model-detail card to reflect a new cover there. */
  canCaptureCover: boolean;
  /** Fix wave finding 4: `ViewerTab`'s `MeshSection` keeps the inline stage
   * mounted while another stage could also be mounted, so without this both
   * stages' `Shift+F` hotkey bindings would fire on one keypress -- both see
   * `document.fullscreenElement === null` (the Fullscreen API is async) and
   * both call `requestFullscreen`, so whichever stage isn't visible can
   * "win" the fullscreen request. Only the currently visible/active stage
   * should bind the hotkey; defaults to `true` since every other caller
   * (the pop-out window) only ever has one stage mounted at a time. */
  active?: boolean;
}

/** Canvas + absolutely-positioned chrome -- the whole redesigned viewer
 * surface (R13a GyroidVault re-chrome). Rendered from the studio surface and
 * the pop-out window with the SAME props (assembled by `useViewerScene`), so
 * both stay in sync. Returns a fragment rather than its own wrapping
 * element: each caller supplies its own height-bearing flex column (the
 * inline surface a `flex flex-col gap-3` div, the window page an `h-svh`
 * column), so the stage becomes a direct flex item of whichever real
 * container it's in without an extra wrapper needing its own `min-h-0
 * flex-1` to pass the height down.
 *
 * Every control the old right-hand panel had still exists -- moved into
 * `ViewerTopOverlay` (Parts, Spin, Cover, Fullscreen), `ViewerDock` (camera
 * preset, shading, quick colors, Grid), and `ViewerMorePanel` (Background,
 * Lighting, Section, Explode, Ortho, Fit, Auto-rotate, Screenshot, Reset
 * colors, AMS sync, New window/Parts in windows, hotkey legend) -- see Key
 * decision 1 ("re-chrome, not re-plumb"): this component still owns none of
 * that state, it only lays the same props out differently. */
export function ViewerStage({
  slug,
  files,
  checkedIds,
  onToggleFile,
  onSetAllChecked,
  colors,
  onSetPartColor,
  onClearPartColor,
  onSetAllColors,
  hasColors,
  onResetColors,
  preset,
  custom,
  background,
  onPresetChange,
  onCustomChange,
  lightingPreset,
  lighting,
  onLightingChange,
  printerId,
  onApplyAmsColors,
  parts,
  checkedList,
  onOpenWindow,
  showWindowButtons,
  variant,
  tools,
  onToolsChange,
  stats,
  onStats,
  plateSize,
  fitSignal,
  onFit,
  viewerApiRef,
  coverUrl,
  onCaptureCover,
  capturingCover,
  canCaptureCover,
  active = true,
}: ViewerStageProps) {
  const handleAutoRotateToggle = useCallback(() => {
    onToolsChange({ autoRotate: !tools.autoRotate });
  }, [onToolsChange, tools.autoRotate]);

  // `shading` is an enum, not an independent boolean per mode -- toggling
  // Wireframe just switches straight to/from "wireframe" regardless of
  // whichever mode (including "xray") was active. Kept for the `W` hotkey;
  // `ViewerDock`'s shading segmented control sets the enum directly.
  const handleWireframeToggle = useCallback(() => {
    onToolsChange({ shading: tools.shading === "wireframe" ? "solid" : "wireframe" });
  }, [onToolsChange, tools.shading]);

  const handleGridToggle = useCallback(() => {
    onToolsChange({ grid: !tools.grid });
  }, [onToolsChange, tools.grid]);

  const handleQuickColor = useCallback(
    (hex: string) => onSetAllColors(hex),
    [onSetAllColors],
  );

  // The explode slider leaves a nonzero offset applied to whichever parts
  // were already loaded when it moved -- a part that finishes loading LATE
  // (checked after the initial eager load, or a newly-added file) would
  // otherwise render at its un-exploded position, visibly detached from the
  // rest of the already-exploded scene. Resetting to 0 on every load keeps
  // the explode state honest: it only ever describes parts that were all
  // present when the slider last moved. A no-op during the initial eager
  // load, since `tools.explode` starts at 0 -- see `ModelViewer`'s
  // `onPartLoaded` doc comment.
  // R9-D item 8: the "GLB has loaded" half of the thumbnail crossfade --
  // `firstLoadRef` guards against `handlePartLoaded` firing again on a LATER
  // part (multi-part scenes) re-triggering the fade, since only the FIRST
  // part to load ends the "still loading" state the thumbnail covers.
  const firstLoadRef = useRef(false);
  const [modelReady, setModelReady] = useState(false);
  const handlePartLoaded = useCallback(() => {
    if (!firstLoadRef.current) {
      firstLoadRef.current = true;
      setModelReady(true);
    }
    if (tools.explode !== 0) onToolsChange({ explode: 0 });
  }, [tools.explode, onToolsChange]);

  // R9-D item 8: keeps the thumbnail `<img>` mounted for the ~250ms fade
  // (Tailwind `transition-opacity`) after `modelReady` flips, then unmounts
  // it -- a plain timer rather than an `onTransitionEnd` handler because
  // `prefers-reduced-motion` (via `motion-reduce:transition-none`) removes
  // the CSS transition entirely, which would never fire that event.
  const [thumbnailMounted, setThumbnailMounted] = useState(true);
  useEffect(() => {
    if (!modelReady) return;
    const id = window.setTimeout(() => setThumbnailMounted(false), 250);
    return () => window.clearTimeout(id);
  }, [modelReady]);

  // Fix wave finding 3: `thumbnailMounted` used to clear ONLY off
  // `modelReady`, which only ever flipped from `handlePartLoaded` (a load
  // SUCCESS). A canvas-level crash (`ViewerErrorBoundary`) rendered its
  // "Preview failed to load" fallback underneath this cover, which kept
  // painting an opaque thumbnail + spinner over it forever -- the user never
  // saw the error. `handleLoadError` drops the cover immediately (no fade,
  // unlike the success path) so the error card is never hidden behind it
  // even briefly.
  const handleLoadError = useCallback(() => {
    firstLoadRef.current = true;
    setModelReady(true);
    setThumbnailMounted(false);
  }, []);

  // Safety net for failure modes that never reach `ViewerErrorBoundary` at
  // all -- e.g. every part unchecked before the first load, or a per-part
  // load failure that `ModelViewer`'s `PartErrorBoundary` swallows locally
  // (renders `null`, never throws up to this boundary) -- either of which
  // would otherwise leave the cover mounted with no load/error signal ever
  // firing. If nothing has resolved the loading state within 8s, drop the
  // cover so the user at least sees the canvas underneath instead of a
  // frozen thumbnail.
  useEffect(() => {
    if (modelReady || !coverUrl || parts.length === 0) return;
    const id = window.setTimeout(() => setThumbnailMounted(false), 8000);
    return () => window.clearTimeout(id);
  }, [modelReady, coverUrl, parts.length]);

  // Explode classification reported by `ModelViewer` once parts load --
  // "none" until then (and for single-part / degenerate scenes), which keeps
  // the control hidden. Drives `ViewerMorePanel`'s Explode/Separate block.
  const [explodeMode, setExplodeMode] = useState<ExplodeMode>("none");

  // `viewerApiRef.current` is populated by `ModelViewer`'s `CaptureBridge`
  // only once the canvas has mounted -- `?.` guards the (brief) window
  // before that effect runs, or the empty-parts placeholder case where
  // `MeshCanvas` never renders a `ModelViewer` at all.
  const handleScreenshot = useCallback(async () => {
    const blob = await viewerApiRef.current?.screenshot();
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${slug}.png`;
    a.click();
    URL.revokeObjectURL(url);
  }, [viewerApiRef, slug]);

  // `F`/`R`/`W`/`G` shortcuts on the canvas wrapper -- ignored while any
  // modifier is held (so `Ctrl+F`/`Cmd+R`/etc. keep their browser-native
  // meaning instead of being hijacked). `stopPropagation` on a match keeps
  // a plain "f" from also bubbling to the ModelHeader's document-level `f`
  // favorite-toggle hotkey (R9-C item 5) -- without it, fitting the view
  // here would also toggle the model's favorite.
  const handleCanvasKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
      if (event.key === "f" || event.key === "F") {
        event.stopPropagation();
        onFit();
      } else if (event.key === "r" || event.key === "R") {
        event.stopPropagation();
        handleAutoRotateToggle();
      } else if (event.key === "w" || event.key === "W") {
        event.stopPropagation();
        handleWireframeToggle();
      } else if (event.key === "g" || event.key === "G") {
        event.stopPropagation();
        handleGridToggle();
      }
    },
    [onFit, handleAutoRotateToggle, handleWireframeToggle, handleGridToggle],
  );

  // R9-C item 5: `Shift+F` fullscreens the stage container via the
  // Fullscreen API. Document-level (via `useHotkeys`) rather than on the
  // canvas wrapper's own `onKeyDown` like the shortcuts above, since the
  // wrapper needing focus first would make this harder to discover than the
  // other viewer controls. Bound only while this component is mounted.
  const stageRef = useRef<HTMLDivElement | null>(null);
  const { isFullscreen, toggleFullscreen } = useStageFullscreen(stageRef, active);

  // Risk resolution 2: the More popover/sheet portals INTO the stage element
  // (not `document.body`) so it still renders while `stageRef.current` is
  // the fullscreened element -- a `document.body` portal renders outside a
  // fullscreened element and so becomes invisible. `stageRef.current` isn't
  // available on the first render (ref not yet attached), so this is state
  // set from an effect rather than read directly, letting the popover/sheet
  // re-render once the container exists.
  const [portalContainer, setPortalContainer] = useState<HTMLElement | null>(null);
  useEffect(() => {
    setPortalContainer(stageRef.current);
  }, []);

  return (
    <TooltipProvider>
      <div
        ref={stageRef}
        className={cn(
          "relative overflow-hidden rounded-lg border border-border bg-card outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
          variant === "inline" ? "h-[450px]" : "h-full min-h-0 flex-1",
          isFullscreen && "h-screen",
        )}
        tabIndex={0}
        onKeyDown={handleCanvasKeyDown}
      >
        <MeshCanvas
          parts={parts}
          background={background}
          lighting={lighting}
          tools={tools}
          plateSize={plateSize}
          onStats={onStats}
          fitSignal={fitSignal}
          apiRef={viewerApiRef}
          onPartLoaded={handlePartLoaded}
          onExplodeModeChange={setExplodeMode}
          onCameraPresetClear={() => onToolsChange({ cameraPreset: null })}
          hasCoverThumbnail={Boolean(coverUrl) && parts.length > 0}
          onError={handleLoadError}
        />
        {coverUrl && parts.length > 0 && thumbnailMounted && (
          <img
            src={coverUrl}
            alt=""
            aria-hidden="true"
            data-testid="viewer-thumbnail"
            className={cn(
              "pointer-events-none absolute inset-0 h-full w-full object-cover transition-opacity duration-[250ms] motion-reduce:transition-none",
              modelReady ? "opacity-0" : "opacity-100",
            )}
          />
        )}
        {coverUrl && parts.length > 0 && !modelReady && (
          <div
            data-testid="viewer-loading-indicator"
            className="pointer-events-none absolute top-2 right-2 rounded-full bg-background/70 p-1.5 backdrop-blur-sm"
          >
            <LoaderCircleIcon className="size-4 animate-spin text-muted-foreground" />
          </div>
        )}

        {/* Overlays: root is `pointer-events-none`, each interactive island
            opts back in with `pointer-events-auto` (risk resolution 3) so
            orbit drags reach the canvas everywhere the dock/overlay don't
            cover. */}
        <ViewerTopOverlay
          stats={stats}
          files={files}
          checkedIds={checkedIds}
          onToggleFile={onToggleFile}
          onSetAllChecked={onSetAllChecked}
          colors={colors}
          onSetPartColor={onSetPartColor}
          onClearPartColor={onClearPartColor}
          autoRotate={tools.autoRotate}
          onToggleAutoRotate={handleAutoRotateToggle}
          onCaptureCover={onCaptureCover}
          capturingCover={capturingCover}
          canCaptureCover={canCaptureCover}
          isFullscreen={isFullscreen}
          onToggleFullscreen={toggleFullscreen}
          container={portalContainer}
        />
        <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center px-2">
          <ViewerDock
            tools={tools}
            onToolsChange={onToolsChange}
            checkedList={checkedList}
            onQuickColor={handleQuickColor}
            morePanel={
              <ViewerMorePanel
                preset={preset}
                custom={custom}
                onPresetChange={onPresetChange}
                onCustomChange={onCustomChange}
                lightingPreset={lightingPreset}
                onLightingChange={onLightingChange}
                tools={tools}
                onToolsChange={onToolsChange}
                explodeMode={explodeMode}
                onFit={onFit}
                onScreenshot={handleScreenshot}
                hasColors={hasColors}
                onResetColors={onResetColors}
                printerId={printerId}
                checkedList={checkedList}
                onApplyAmsColors={onApplyAmsColors}
                onOpenWindow={onOpenWindow}
                showWindowButtons={showWindowButtons}
                container={portalContainer}
              />
            }
          />
        </div>
      </div>
      <ViewerFooterStrip files={files} checkedIds={checkedIds} />
    </TooltipProvider>
  );
}
