import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { Loader2, FileWarning } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  filterLayersByType,
  rekeyLayers,
  ToolpathType,
  type ParsedToolpath,
  type ToolpathStats,
} from "@/lib/gcodeToolpath";
import { parseGcodeAsync } from "@/lib/gcodeParseAsync";
import { buildSegmentData, makeToolpath, computeColors, TYPE_COLOR, DEFAULT_RANGES_COLORS } from "@/lib/vendor/toolpathRenderer.js";

export type GcodeColorMode = "feature" | "tool" | "speed" | "layer";
export type GcodeLoadedMeta = ToolpathStats & { bounds: ParsedToolpath["bounds"] };

export interface GcodeToolpathViewerProps {
  gcodeUrl: string;
  className?: string;
  initialColorMode?: GcodeColorMode;
  onLoaded?: (meta: GcodeLoadedMeta) => void;
}

const MODES: GcodeColorMode[] = ["feature", "tool", "speed", "layer"];
const VOLUME = { x: 256, y: 256 };

/** Feature toggles: a toggle may cover several parser types. */
const FEATURES: Array<{ key: string; types: number[] }> = [
  { key: "wall", types: [ToolpathType.wall, ToolpathType.thinWall] },
  { key: "infill", types: [ToolpathType.sparseInfill, ToolpathType.solidInfill, ToolpathType.gapFill] },
  { key: "support", types: [ToolpathType.support, ToolpathType.raft] },
  { key: "bridge", types: [ToolpathType.bridge] },
  { key: "skirt", types: [ToolpathType.skirt] },
];

const TOOL_PALETTE = [0x00ae42, 0xe8a317, 0x3b82f6, 0xe5484d, 0xa855f7, 0x14b8a6, 0xf472b6, 0x9ca3af];
const pack = (rgb: number[]) => (Math.round(rgb[0] * 255) << 16) | (Math.round(rgb[1] * 255) << 8) | Math.round(rgb[2] * 255);
const css = (rgb: number[] | undefined) => (rgb ? `rgb(${rgb.map((c) => Math.round(c * 255)).join(",")})` : "transparent");
const hex = (n: number) => `#${n.toString(16).padStart(6, "0")}`;
const ramp = (DEFAULT_RANGES_COLORS as number[][]).map((c, i, a) => `${css(c)} ${Math.round((i / (a.length - 1)) * 100)}%`).join(", ");

function formatDuration(s: number): string {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m ${Math.floor(s % 60)}s`;
}

/** jsdom / ancient browsers: treat the viewer as always visible. */
class NoopObserver {
  observe() {}
  disconnect() {}
}

type LoadState = "loading" | "ready" | "empty" | "error";

export default function GcodeToolpathViewer({ gcodeUrl, className = "", initialColorMode = "feature", onLoaded }: GcodeToolpathViewerProps) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [parsed, setParsed] = useState<ParsedToolpath | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [mode, setMode] = useState<GcodeColorMode>(initialColorMode);
  const [range, setRange] = useState<[number, number]>([0, 0]);
  const [hiddenKeys, setHiddenKeys] = useState<ReadonlySet<string>>(new Set());
  const [showTravel, setShowTravel] = useState(false);
  const [sceneReady, setSceneReady] = useState(false);
  const [generation, setGeneration] = useState(0);

  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const handleRef = useRef<ReturnType<typeof makeToolpath> | null>(null);
  const dataRef = useRef<ReturnType<typeof buildSegmentData> | null>(null);
  const framedRef = useRef(false);
  const invalidateRef = useRef<(() => void) | null>(null);
  const rangeRef = useRef(range);
  const travelRef = useRef(showTravel);
  const onLoadedRef = useRef(onLoaded);
  rangeRef.current = range;
  travelRef.current = showTravel;
  onLoadedRef.current = onLoaded;

  // --- fetch + parse ---
  useEffect(() => {
    const ctrl = new AbortController();
    setState("loading");
    setParsed(null);
    (async () => {
      try {
        const res = await fetch(gcodeUrl, { credentials: "include", signal: ctrl.signal });
        if (res.status === 404) return setState("empty");
        if (!res.ok) throw new Error(String(res.status));
        const result = await parseGcodeAsync(await res.text(), ctrl.signal);
        if (ctrl.signal.aborted) return;
        if (result.layers.length === 0) return setState("empty");
        framedRef.current = false;
        setParsed(result);
        setRange([0, result.layers.length - 1]);
        setState("ready");
        onLoadedRef.current?.({ ...result.stats, bounds: result.bounds });
      } catch (err) {
        if (!ctrl.signal.aborted && (err as Error).name !== "AbortError") setState("error");
      }
    })();
    return () => ctrl.abort();
  }, [gcodeUrl, attempt]);

  // --- scene (once) ---
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const w = container.clientWidth || 1;
    const h = container.clientHeight || 1;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x1a1a1a);
    const camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 10000);
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(w, h);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    // Out of flow so the canvas cannot size the container it is measured against.
    Object.assign(renderer.domElement.style, { display: "block", position: "absolute", inset: "0" });
    container.appendChild(renderer.domElement);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    const grid = new THREE.GridHelper(Math.max(VOLUME.x, VOLUME.y), 16, 0x444444, 0x333333);
    // Toolpath group is rotated -90deg about X: slicer +Y runs along world -Z.
    grid.position.set(VOLUME.x / 2, 0, -VOLUME.y / 2);
    scene.add(grid);
    sceneRef.current = scene;
    cameraRef.current = camera;
    controlsRef.current = controls;
    setSceneReady(true);

    // Render on demand: one rAF per change (controls 'change', damping tail,
    // resize, data updates), paused while offscreen / tab hidden.
    let frame = 0;
    let visible = true;
    let disposed = false;
    const renderFrame = () => {
      frame = 0;
      if (disposed || !visible || document.hidden) return;
      // update() returns true while damping is still moving the camera.
      if (controls.update()) schedule();
      renderer.render(scene, camera);
    };
    const schedule = () => {
      if (frame || disposed || !visible || document.hidden) return;
      frame = requestAnimationFrame(renderFrame);
    };
    invalidateRef.current = schedule;
    controls.addEventListener("change", schedule);
    const resize = () => {
      const cw = container.clientWidth || 1;
      const ch = container.clientHeight || 1;
      camera.aspect = cw / ch;
      camera.updateProjectionMatrix();
      renderer.setSize(cw, ch);
      schedule();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    const io = new (globalThis.IntersectionObserver ?? NoopObserver)((entries) => {
      visible = entries[entries.length - 1]?.isIntersecting ?? true;
      if (visible) schedule();
      else if (frame) {
        cancelAnimationFrame(frame);
        frame = 0;
      }
    });
    io.observe(container);
    const onVisibility = () => {
      if (!document.hidden) schedule();
    };
    document.addEventListener("visibilitychange", onVisibility);
    const onLost = (e: Event) => e.preventDefault();
    renderer.domElement.addEventListener("webglcontextlost", onLost);
    renderer.domElement.addEventListener("webglcontextrestored", schedule);
    schedule();
    return () => {
      disposed = true;
      invalidateRef.current = null;
      observer.disconnect();
      io.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      renderer.domElement.removeEventListener("webglcontextlost", onLost);
      renderer.domElement.removeEventListener("webglcontextrestored", schedule);
      controls.removeEventListener("change", schedule);
      cancelAnimationFrame(frame);
      controls.dispose();
      grid.geometry.dispose();
      (grid.material as THREE.Material).dispose();
      renderer.forceContextLoss();
      renderer.dispose();
      container.removeChild(renderer.domElement);
      sceneRef.current = cameraRef.current = controlsRef.current = null;
      setSceneReady(false);
    };
  }, []);

  const hiddenTypes = useMemo(() => {
    const s = new Set<number>();
    for (const f of FEATURES) if (hiddenKeys.has(f.key)) f.types.forEach((x) => s.add(x));
    return s;
  }, [hiddenKeys]);
  const hiddenSig = [...hiddenKeys].sort().join(",");
  const keyedMode = mode === "feature" ? null : mode;

  // --- toolpath mesh: rebuilt when the vertex layout changes ---
  useEffect(() => {
    const scene = sceneRef.current;
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!scene || !camera || !controls || !parsed) return;
    const filtered = filterLayersByType(parsed.layers, hiddenTypes);
    const layers = keyedMode ? rekeyLayers(filtered, keyedMode, parsed.stats.speedRange) : filtered;
    const data = buildSegmentData(layers, parsed.defaultWidth);
    const handle = makeToolpath(THREE, data);
    dataRef.current = data;
    handleRef.current = handle;
    const group = new THREE.Group();
    group.rotation.x = -Math.PI / 2;
    group.add(handle.mesh, handle.travLines);
    scene.add(group);
    handle.setTravelVisible(travelRef.current);
    handle.setLayerRange(rangeRef.current[0], rangeRef.current[1]);
    setGeneration((n) => n + 1);

    if (!framedRef.current) {
      framedRef.current = true;
      // Bounds come from the parser: Box3.setFromObject only sees the diamond template.
      const b = parsed.bounds;
      const box = b
        ? new THREE.Box3(new THREE.Vector3(b.min[0], b.min[2], -b.max[1]), new THREE.Vector3(b.max[0], b.max[2], -b.min[1]))
        : new THREE.Box3(new THREE.Vector3(0, 0, 0), new THREE.Vector3(VOLUME.x, 1, -VOLUME.y));
      const center = box.getCenter(new THREE.Vector3());
      const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 0.001);
      const vFov = THREE.MathUtils.degToRad(camera.fov);
      const hFov = 2 * Math.atan(Math.tan(vFov / 2) * camera.aspect);
      const dist = 1.15 * Math.max(radius / Math.sin(vFov / 2), radius / Math.sin(hFov / 2));
      camera.position.copy(center).addScaledVector(new THREE.Vector3(0.7, 0.5, 0.7).normalize(), dist);
      camera.near = Math.max(dist / 1000, 0.01);
      camera.far = dist + radius * 4;
      camera.updateProjectionMatrix();
      controls.target.copy(center);
      controls.update();
    }
    invalidateRef.current?.();
    return () => {
      scene.remove(group);
      handle.dispose();
      invalidateRef.current?.();
      handleRef.current = null;
      dataRef.current = null;
    };
    // hiddenSig stands in for the Set identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [parsed, keyedMode, hiddenSig, sceneReady]);

  useEffect(() => {
    handleRef.current?.setLayerRange(range[0], range[1]);
    invalidateRef.current?.();
  }, [range]);
  useEffect(() => {
    handleRef.current?.setTravelVisible(showTravel);
    invalidateRef.current?.();
  }, [showTravel]);

  // --- colours ---
  const colorInfo = useMemo(() => {
    const data = dataRef.current;
    if (!data || !parsed) return null;
    if (mode === "feature") return { color: computeColors(data, "feature", {}).color };
    const colors = new Float32Array(data.nV * 4);
    for (let v = 0; v < data.nV; v += 1) {
      const key = Math.max(0, data.meta.vType[v] - 1);
      colors[v * 4] = mode === "tool" ? TOOL_PALETTE[key % TOOL_PALETTE.length] : pack((DEFAULT_RANGES_COLORS as number[][])[Math.min(key, 10)]);
    }
    return { color: colors };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, parsed, generation, hiddenSig]);
  useEffect(() => {
    if (colorInfo) handleRef.current?.setColors(colorInfo.color);
    invalidateRef.current?.();
  }, [colorInfo]);

  const toggle = useCallback((key: string) => {
    setHiddenKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const layerCount = parsed?.layers.length ?? 0;
  const stats = parsed?.stats;
  const featureColor = (types: number[]) => css(TYPE_COLOR[types[0]]);

  return (
    <div className={`relative flex min-h-[320px] flex-col ${className}`}>
      <div className="relative min-h-0 flex-1">
        <div ref={containerRef} className="absolute inset-0" />

        {state === "loading" && (
          <div className="absolute inset-0 flex items-center justify-center gap-2 bg-background/60 text-sm text-muted-foreground" role="status">
            <Loader2 className="size-4 animate-spin" />
            {t("gcodeViewer.loading")}
          </div>
        )}
        {state === "empty" && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-background text-sm text-muted-foreground">
            <FileWarning className="size-8" />
            {t("gcodeViewer.empty")}
          </div>
        )}
        {state === "error" && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-background text-sm text-muted-foreground">
            <FileWarning className="size-8" />
            {t("gcodeViewer.loadFailed")}
            <Button size="sm" variant="outline" onClick={() => setAttempt((n) => n + 1)}>
              {t("gcodeViewer.retry")}
            </Button>
          </div>
        )}

        {state === "ready" && (
          <>
            <div className="absolute left-3 top-3 flex max-w-[11rem] flex-col gap-2 rounded border bg-background/85 p-2 text-xs">
              <select
                value={mode}
                onChange={(e) => setMode(e.target.value as GcodeColorMode)}
                aria-label={t("gcodeViewer.colorMode")}
                className="rounded border bg-background px-1 py-1"
              >
                {MODES.map((m) => (
                  <option key={m} value={m}>
                    {t(`gcodeViewer.mode.${m}`)}
                  </option>
                ))}
              </select>

              <ul className="flex flex-col gap-0.5">
                {FEATURES.map((f) => (
                  <li key={f.key}>
                    <label className="flex cursor-pointer items-center gap-1.5">
                      <input type="checkbox" checked={!hiddenKeys.has(f.key)} onChange={() => toggle(f.key)} />
                      {mode === "feature" && <span className="inline-block size-2.5 rounded-sm" style={{ backgroundColor: featureColor(f.types) }} aria-hidden />}
                      {t(`gcodeViewer.feature.${f.key}`)}
                    </label>
                  </li>
                ))}
                <li>
                  <label className="flex cursor-pointer items-center gap-1.5">
                    <input type="checkbox" checked={showTravel} onChange={(e) => setShowTravel(e.target.checked)} />
                    {t("gcodeViewer.feature.travel")}
                  </label>
                </li>
              </ul>

              {mode === "tool" && stats && (
                <ul className="flex flex-col gap-0.5" aria-label={t("gcodeViewer.legend")}>
                  {stats.tools.map((tool) => (
                    <li key={tool} className="flex items-center gap-1.5">
                      <span className="inline-block size-2.5 rounded-sm" style={{ backgroundColor: hex(TOOL_PALETTE[tool % TOOL_PALETTE.length]) }} aria-hidden />
                      {t("gcodeViewer.tool", { n: tool + 1 })}
                    </li>
                  ))}
                </ul>
              )}
              {(mode === "speed" || mode === "layer") && stats && (
                <div className="flex flex-col gap-1" aria-label={t("gcodeViewer.legend")}>
                  <div className="h-2.5 rounded-sm border" style={{ background: `linear-gradient(to right, ${ramp})` }} aria-hidden />
                  <div className="flex justify-between tabular-nums text-muted-foreground">
                    {mode === "speed" ? (
                      <>
                        <span>{stats.speedRange ? Math.round(stats.speedRange[0] / 60) : "-"}</span>
                        <span>{t("gcodeViewer.speedUnit")}</span>
                        <span>{stats.speedRange ? Math.round(stats.speedRange[1] / 60) : "-"}</span>
                      </>
                    ) : (
                      <>
                        <span>1</span>
                        <span>{t("gcodeViewer.layerWord")}</span>
                        <span>{layerCount}</span>
                      </>
                    )}
                  </div>
                </div>
              )}
            </div>

            <div className="absolute right-3 top-3 flex flex-col items-center gap-1 rounded border bg-background/85 p-2">
              <span className="text-[0.65rem] tabular-nums text-muted-foreground">{range[1] + 1}</span>
              <input
                type="range"
                min={0}
                max={Math.max(0, layerCount - 1)}
                value={range[1]}
                onChange={(e) => {
                  const top = Number(e.target.value);
                  setRange(([lo]) => [Math.min(lo, top), top]);
                }}
                aria-label={t("gcodeViewer.topLayer")}
                className="h-40 w-4 cursor-pointer"
                style={{ writingMode: "vertical-lr", direction: "rtl" }}
              />
              <input
                type="range"
                min={0}
                max={Math.max(0, layerCount - 1)}
                value={range[0]}
                onChange={(e) => {
                  const lo = Number(e.target.value);
                  setRange(([, hi]) => [lo, Math.max(lo, hi)]);
                }}
                aria-label={t("gcodeViewer.bottomLayer")}
                className="h-40 w-4 cursor-pointer"
                style={{ writingMode: "vertical-lr", direction: "rtl" }}
              />
              <span className="text-[0.65rem] tabular-nums text-muted-foreground">{range[0] + 1}</span>
            </div>
          </>
        )}
      </div>

      {state === "ready" && stats && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 border-t px-3 py-1.5 text-xs tabular-nums text-muted-foreground" data-testid="gcode-stats">
          <span>{t("gcodeViewer.stats.layers", { count: stats.layers })}</span>
          <span>{t("gcodeViewer.stats.moves", { count: stats.moves })}</span>
          <span>{t("gcodeViewer.stats.extrusion", { m: (stats.extrusionLength / 1000).toFixed(2) })}</span>
          {stats.estimatedSeconds !== null && <span>{t("gcodeViewer.stats.time", { time: formatDuration(stats.estimatedSeconds) })}</span>}
        </div>
      )}
    </div>
  );
}
