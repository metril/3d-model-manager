/**
 * Live camera tile (P4 Task 8, rewritten for Q4): mints a scoped camera
 * token (`useCameraUrls`) once the printer reports `online`, then streams
 * the feed as a plain `<img>` (MJPEG). "Pause" is enforced by unmounting
 * the img: off-screen (`IntersectionObserver`) or while the tab is hidden,
 * which lets printerd's camera worker idle-stop. There is no backend stop
 * route, so unmount also drops the img `src` to close the connection.
 *
 * Q4 adds: wheel/pinch zoom + drag pan (double-click resets), rotation from
 * `GET /camera/status` (CSS transform), fullscreen, a light toggle (control
 * perm only), stream/snapshot mode and fps selection, a "beta" badge, and a
 * stall watchdog: an `<img>` MJPEG has no per-frame event, so every second
 * the current frame is sampled onto a 64x64 canvas; 20 s of byte-identical
 * frames (or no first frame) reconnects with exponential backoff 5s..60s
 * (reset when a frame changes).
 *
 * `urls` / `rotation` let token-only contexts (OBS overlay, camwall tiles)
 * bypass the session-only token + status calls.
 */
import {
  ExpandIcon,
  LightbulbIcon,
  MinimizeIcon,
  RotateCcwIcon,
  VideoOffIcon,
  VideoIcon,
  ImageIcon,
  ZoomInIcon,
  ZoomOutIcon,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useAuth } from "@/api/auth";
import { useCameraLight, useCameraStatus, useCameraUrls } from "@/api/printers";
import { Skeleton } from "@/components/ui/skeleton";
import { useElementFullscreen } from "@/hooks/useElementFullscreen";
import { cn } from "@/lib/utils";

const RETRY_MS = 10_000;
const STALL_MS = 20_000;
const SAMPLE_PX = 64;
const FPS_CHOICES = [1, 2, 5, 10, 15];
const MIN_ZOOM = 1;
const MAX_ZOOM = 8;

/** Visible enough to keep the MJPEG `<img>` mounted: on-screen past the
 * 0.1 IntersectionObserver threshold, and the tab itself isn't hidden. */
function useVisible(target: React.RefObject<HTMLElement | null>): boolean {
  const [intersecting, setIntersecting] = useState(true);
  const [hidden, setHidden] = useState(() => document.visibilityState === "hidden");

  useEffect(() => {
    if (typeof IntersectionObserver === "undefined" || !target.current) return;
    const observer = new IntersectionObserver(([entry]) => setIntersecting(entry?.isIntersecting ?? true), {
      threshold: 0.1,
    });
    observer.observe(target.current);
    return () => observer.disconnect();
  }, [target]);

  useEffect(() => {
    const onVisibility = () => setHidden(document.visibilityState === "hidden");
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  return intersecting && !hidden;
}

function withParam(url: string, key: string, value: string | number): string {
  return `${url}${url.includes("?") ? "&" : "?"}${key}=${value}`;
}

/** Stalled when no frame has decoded for `stallMs` after connect, or the
 * decoded frame has been byte-identical for `stallMs`. */
export function isStreamStalled(
  naturalWidth: number,
  sinceConnectMs: number,
  sinceChangeMs: number,
  stallMs: number,
): boolean {
  if (naturalWidth === 0) return sinceConnectMs > stallMs;
  return sinceChangeMs > stallMs;
}

/** Reconnect delay after the n-th (0-based) consecutive stall: 5,10,20,40,60s. */
export function reconnectDelayMs(attempt: number): number {
  return Math.min(5_000 * 2 ** attempt, 60_000);
}

/** Exact byte comparison of two frame samples. */
export function framesEqual(a: Uint8ClampedArray, b: Uint8ClampedArray): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

/** Downsampled RGBA of the img's current frame; `null` when nothing is
 * decoded yet or canvas sampling is unavailable. */
function sampleFrame(img: HTMLImageElement | null): Uint8ClampedArray | null {
  if (!img || img.naturalWidth === 0) return null;
  try {
    const canvas = document.createElement("canvas");
    canvas.width = SAMPLE_PX;
    canvas.height = SAMPLE_PX;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) return null;
    ctx.drawImage(img, 0, 0, SAMPLE_PX, SAMPLE_PX);
    return new Uint8ClampedArray(ctx.getImageData(0, 0, SAMPLE_PX, SAMPLE_PX).data);
  } catch {
    return null;
  }
}

interface View {
  s: number;
  x: number;
  y: number;
}

const clampZoom = (s: number) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, s));

export interface CameraTileProps {
  printerId: number;
  online: boolean;
  className?: string;
  /** Snapshot polling only (camwall's non-live tiles). */
  snapshotOnly?: boolean;
  /** Snapshot refresh seconds (default 2, or 10 when `snapshotOnly`). */
  snapshotSec?: number;
  /** Pre-signed URLs (overlay/token contexts): skips token + status calls. */
  urls?: { stream_url: string; snapshot_url: string };
  /** Overrides the rotation from camera status. */
  rotation?: number;
  /** Show the hover toolbar (default true). */
  controls?: boolean;
}

export function CameraTile({
  printerId,
  online,
  className,
  snapshotOnly = false,
  snapshotSec,
  urls,
  rotation: rotationProp,
  controls = true,
}: CameraTileProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const lastImg = useRef<HTMLImageElement | null>(null);
  const visible = useVisible(containerRef);
  const fullscreen = useElementFullscreen(containerRef);

  const [loaded, setLoaded] = useState(false);
  const [errored, setErrored] = useState(false);
  const [mode, setMode] = useState<"stream" | "snapshot">(snapshotOnly ? "snapshot" : "stream");
  const [fpsOverride, setFpsOverride] = useState<number | null>(null);
  const [nonce, setNonce] = useState(0);
  const [tick, setTick] = useState(0);
  const [view, setView] = useState<View>({ s: 1, x: 0, y: 0 });
  const [box, setBox] = useState({ w: 0, h: 0 });
  const attempts = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const tokens = useCameraUrls(printerId, { enabled: online && !urls });
  const status = useCameraStatus(printerId, { enabled: online && !urls });
  const refetchToken = tokens.refetch;
  const effective = urls ?? tokens.data;

  const rotation = (((rotationProp ?? status.data?.rotation ?? 0) % 360) + 360) % 360;
  const fps = fpsOverride ?? status.data?.default_fps;
  // The backend rotates snapshots itself; only the MJPEG stream needs CSS rotation.
  const cssRotation = mode === "stream" ? rotation : 0;
  const sideways = cssRotation === 90 || cssRotation === 270;
  const hasUrls = urls != null;
  const noCamera = status.data?.source === "none";

  // Auto-retry (10s) after a stream error: refetch a fresh token and give
  // the `<img>` another try.
  useEffect(() => {
    if (!errored) return;
    const timer = setTimeout(() => {
      setErrored(false);
      setLoaded(false);
      attempts.current = 0;
      if (!hasUrls) void refetchToken();
      else setNonce((n) => n + 1);
    }, RETRY_MS);
    return () => clearTimeout(timer);
  }, [errored, refetchToken, hasUrls]);

  // A printer going back online clears any stale error from a previous session.
  useEffect(() => {
    if (!online) setErrored(false);
  }, [online]);

  useEffect(() => {
    if (typeof ResizeObserver === "undefined" || !containerRef.current) return;
    const el = containerRef.current;
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const offline = !online || errored;
  const showImg = !offline && visible && effective != null && !noCamera;

  // Snapshot mode: re-pull the still on an interval.
  useEffect(() => {
    if (!showImg || mode !== "snapshot") return;
    const ms = (snapshotSec ?? (snapshotOnly ? 10 : 2)) * 1000;
    const id = setInterval(() => setTick((t) => t + 1), ms);
    return () => clearInterval(id);
  }, [showImg, mode, snapshotSec, snapshotOnly]);

  // Stall watchdog (stream mode only).
  useEffect(() => {
    if (!showImg || mode !== "stream") return;
    const connectedAt = Date.now();
    let lastChange = connectedAt;
    let last: Uint8ClampedArray | null = null;
    const id = setInterval(() => {
      const img = imgRef.current;
      const w = img?.naturalWidth ?? 0;
      const frame = sampleFrame(img);
      if (frame) {
        if (last && !framesEqual(last, frame)) {
          // A changed frame (sensor noise counts) proves the stream is live.
          attempts.current = 0;
          lastChange = Date.now();
        }
        if (!last) lastChange = Date.now();
        last = frame;
      } else if (w > 0) {
        // Sampling unavailable (tainted canvas): decoded frame is all we know.
        lastChange = Date.now();
        attempts.current = 0;
      }
      const now = Date.now();
      if (!isStreamStalled(w, now - connectedAt, now - lastChange, STALL_MS)) return;
      clearInterval(id);
      const a = attempts.current;
      attempts.current = a + 1;
      reconnectTimer.current = setTimeout(() => {
        setLoaded(false);
        setNonce((n) => n + 1);
      }, reconnectDelayMs(a));
    }, 1000);
    return () => clearInterval(id);
  }, [showImg, mode, nonce]);

  // Stop on unmount: no backend stop route, so drop the src to close the socket.
  useEffect(
    () => () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      if (lastImg.current) lastImg.current.src = "";
    },
    [],
  );

  const setImg = useCallback((node: HTMLImageElement | null) => {
    imgRef.current = node;
    if (node) lastImg.current = node;
  }, []);

  let src: string | undefined;
  if (effective) {
    if (mode === "snapshot") {
      src = withParam(effective.snapshot_url, "_t", tick);
    } else {
      src = effective.stream_url;
      if (fps !== undefined) src = withParam(src, "fps", fps);
      if (nonce > 0) src = withParam(src, "_r", nonce);
    }
  }

  // --- zoom / pan / pinch ---
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinchDist = useRef<number | null>(null);

  const zoomTo = (s: number) =>
    setView((v) => {
      const next = clampZoom(s);
      return next === 1 ? { s: 1, x: 0, y: 0 } : { ...v, s: next };
    });

  function onWheel(e: React.WheelEvent) {
    if (!controls) return;
    zoomTo(view.s * Math.exp(-e.deltaY * 0.002));
  }
  function onPointerDown(e: React.PointerEvent) {
    if (!controls) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    try {
      e.currentTarget.setPointerCapture?.(e.pointerId);
    } catch {
      /* synthetic pointer ids in tests */
    }
    pinchDist.current = null;
  }
  function onPointerMove(e: React.PointerEvent) {
    const prev = pointers.current.get(e.pointerId);
    if (!prev) return;
    const cur = { x: e.clientX, y: e.clientY };
    pointers.current.set(e.pointerId, cur);
    if (pointers.current.size >= 2) {
      const [a, b] = [...pointers.current.values()];
      const d = Math.hypot(a!.x - b!.x, a!.y - b!.y);
      if (pinchDist.current) zoomTo(view.s * (d / pinchDist.current));
      pinchDist.current = d;
    } else if (view.s > 1) {
      setView((v) => ({ ...v, x: v.x + (cur.x - prev.x), y: v.y + (cur.y - prev.y) }));
    }
  }
  function onPointerUp(e: React.PointerEvent) {
    pointers.current.delete(e.pointerId);
    pinchDist.current = null;
  }

  const fit = sideways && box.w > 0 && box.h > 0 ? Math.min(box.h / box.w, box.w / box.h) : 1;
  const imgTransform = `rotate(${cssRotation}deg)${sideways ? ` scale(${fit})` : ""}`;
  const stop = (e: React.SyntheticEvent) => e.stopPropagation();

  return (
    <div
      ref={containerRef}
      data-slot="camera-tile"
      className={cn("group relative size-full touch-none overflow-hidden bg-muted", className)}
      style={
        !offline && effective && !noCamera
          ? {
              backgroundImage: `url(${effective.snapshot_url})`,
              backgroundSize: "cover",
              backgroundPosition: "center",
            }
          : undefined
      }
      onWheel={onWheel}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onDoubleClick={() => controls && setView({ s: 1, x: 0, y: 0 })}
    >
      {offline || noCamera ? (
        <div className="flex size-full flex-col items-center justify-center gap-1.5 text-muted-foreground">
          <VideoOffIcon className="size-6" aria-hidden="true" />
          <span className="label-caps text-xs">{noCamera && !offline ? "No camera" : "Camera offline"}</span>
        </div>
      ) : (
        <>
          {showImg ? (
            <div
              data-testid="camera-view"
              className="size-full"
              style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.s})` }}
            >
              <img
                key={`${mode}-${nonce}`}
                ref={setImg}
                src={src}
                alt="Camera feed"
                draggable={false}
                className={cn(
                  "size-full transition-opacity",
                  sideways ? "object-contain" : "object-cover",
                  loaded ? "opacity-100" : "opacity-0",
                )}
                style={cssRotation ? { transform: imgTransform } : undefined}
                onLoad={() => setLoaded(true)}
                onError={() => setErrored(true)}
              />
            </div>
          ) : null}
          {!loaded ? <Skeleton className="absolute inset-0 size-full rounded-none" /> : null}
        </>
      )}

      {status.data?.beta ? (
        <span className="absolute left-1.5 top-1.5 rounded bg-warning/90 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-black">
          beta
        </span>
      ) : null}

      {controls && !offline && !noCamera ? (
        <div
          role="toolbar"
          aria-label="Camera controls"
          className="absolute inset-x-0 bottom-0 flex items-center justify-end gap-0.5 bg-gradient-to-t from-black/60 to-transparent p-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100"
          onPointerDown={stop}
          onDoubleClick={stop}
          onWheel={stop}
        >
          {!urls ? <LightToggle printerId={printerId} /> : null}
          <ToolButton
            label={mode === "stream" ? "Switch to snapshots" : "Switch to live stream"}
            onClick={() => {
              setLoaded(false);
              setMode((m) => (m === "stream" ? "snapshot" : "stream"));
            }}
          >
            {mode === "stream" ? <ImageIcon className="size-4" /> : <VideoIcon className="size-4" />}
          </ToolButton>
          {mode === "stream" ? (
            <select
              aria-label="Frame rate"
              value={fps ?? ""}
              onChange={(e) => {
                setFpsOverride(Number(e.target.value));
                setLoaded(false);
              }}
              className="h-6 rounded bg-black/50 px-1 text-xs text-white"
            >
              {fps === undefined ? <option value="">fps</option> : null}
              {FPS_CHOICES.map((f) => (
                <option key={f} value={f}>
                  {f} fps
                </option>
              ))}
            </select>
          ) : null}
          <ToolButton label="Zoom out" onClick={() => zoomTo(view.s / 1.5)}>
            <ZoomOutIcon className="size-4" />
          </ToolButton>
          <ToolButton label="Zoom in" onClick={() => zoomTo(view.s * 1.5)}>
            <ZoomInIcon className="size-4" />
          </ToolButton>
          <ToolButton label="Reset zoom" onClick={() => setView({ s: 1, x: 0, y: 0 })}>
            <RotateCcwIcon className="size-4" />
          </ToolButton>
          {fullscreen.supported ? (
            <ToolButton label={fullscreen.active ? "Exit fullscreen" : "Fullscreen"} onClick={fullscreen.toggle}>
              {fullscreen.active ? <MinimizeIcon className="size-4" /> : <ExpandIcon className="size-4" />}
            </ToolButton>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** Light button: session-only (token contexts never render it) and only
 * for viewers with `control` on this printer. */
function LightToggle({ printerId }: { printerId: number }) {
  const { canPrinter } = useAuth();
  const light = useCameraLight(printerId);
  const [on, setOn] = useState(false);
  if (!canPrinter(printerId, "control")) return null;
  return (
    <ToolButton
      label={on ? "Light off" : "Light on"}
      pressed={on}
      onClick={() => {
        const next = !on;
        setOn(next);
        light.mutate(next, { onError: () => setOn(!next) });
      }}
    >
      <LightbulbIcon className="size-4" />
    </ToolButton>
  );
}

function ToolButton({
  label,
  onClick,
  pressed,
  children,
}: {
  label: string;
  onClick: () => void;
  pressed?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={pressed}
      title={label}
      onClick={onClick}
      className={cn(
        "grid size-6 place-items-center rounded text-white hover:bg-white/20 focus-visible:outline focus-visible:outline-2",
        pressed && "bg-white/30",
      )}
    >
      {children}
    </button>
  );
}
