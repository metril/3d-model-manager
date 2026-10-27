/**
 * Layer-by-layer g-code preview (R10-B, plan item 10). ``gcode-preview``
 * (a standalone WebGL/three.js library) is heavy the same way
 * three/@react-three/fiber are (Global Constraints "BUNDLE RULE") -- this
 * module must stay `React.lazy`-loaded from `PlatePanel`, behind a
 * "Preview layers" button, so it never lands in the main bundle. It does
 * NOT reuse `ModelViewer`'s R3F scene: `gcode-preview` drives its own
 * `<canvas>`/renderer directly.
 *
 * `buildVolume`/`extrusionColor` are optional overrides (mainly for tests).
 * `PlatePanel` renders this standalone with no printer data threaded down
 * (that plumbing would run `PlatePanel` -> `StudioSurface` -> `FilesTab`,
 * out of scope here), so by default this component is self-sufficient: it
 * reads the first printer's `build_volume_mm` and first colored tray itself
 * via `usePrinters`/`usePrinterStatus`, same as `useViewerScene` does for
 * the R3F viewer's plate size.
 */
import { useEffect, useRef, useState } from "react";
import type { WebGLPreview } from "gcode-preview";

import { usePrinters, usePrinterStatus } from "@/api/printers";

const DEFAULT_BUILD_VOLUME = { x: 256, y: 256, z: 256 };

/** GET /api/files/{id}/download?member=gcode (R10-B) — the backend
 * extracts a `.gcode.3mf`'s embedded plate gcode; a bare `.gcode` file
 * would just stream itself, though `PlatePanel` only mounts this for
 * sliced files today. */
function gcodeDownloadUrl(fileId: number): string {
  return `/api/files/${fileId}/download?member=gcode`;
}

/** Above this, don't even try to load the gcode text into the browser
 * (review finding 1): `gcode-preview` needs the whole body as one string,
 * and a huge sliced-project plate can be hundreds of MB -- reading
 * `response.text()` on that risks hanging/crashing the tab. */
const TOO_LARGE_TO_PREVIEW_BYTES = 150 * 1024 * 1024;

export function GcodePreview({
  fileId,
  buildVolume,
  extrusionColor,
}: {
  fileId: number;
  buildVolume?: { x: number; y: number; z: number };
  extrusionColor?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const previewRef = useRef<WebGLPreview | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error" | "too-large">("loading");
  const [layerCount, setLayerCount] = useState(0);
  const [layer, setLayer] = useState(1);

  const printers = usePrinters();
  const firstPrinterId = printers.data?.[0]?.id;
  const printerStatus = usePrinterStatus(firstPrinterId ?? -1, { enabled: firstPrinterId !== undefined });

  const resolvedBuildVolume = buildVolume ?? printers.data?.[0]?.build_volume_mm ?? DEFAULT_BUILD_VOLUME;
  // Mirrors `ViewerMorePanel.tsx`'s `AmsSync`: first tray reporting a
  // truthy `color`, or undefined when none does.
  const resolvedExtrusionColor =
    extrusionColor ?? (printerStatus.data?.trays ?? []).find((tray) => tray.color)?.color ?? undefined;

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setStatus("loading");
      try {
        const [{ init }, response] = await Promise.all([
          import("gcode-preview"),
          fetch(gcodeDownloadUrl(fileId), { credentials: "include" }),
        ]);
        if (!response.ok) throw new Error(`gcode download failed: ${response.status}`);

        const contentLength = Number(response.headers.get("content-length"));
        if (Number.isFinite(contentLength) && contentLength > TOO_LARGE_TO_PREVIEW_BYTES) {
          if (!cancelled) setStatus("too-large");
          return;
        }

        const text = await response.text();
        if (cancelled || !canvasRef.current) return;

        const preview = init({
          canvas: canvasRef.current,
          buildVolume: resolvedBuildVolume,
          ...(resolvedExtrusionColor ? { extrusionColor: resolvedExtrusionColor } : {}),
        });
        preview.processGCode(text);
        preview.render();
        previewRef.current = preview;

        const total = preview.maxLayerIndex + 1;
        setLayerCount(total);
        setLayer(total);
        setStatus("ready");
      } catch {
        if (!cancelled) setStatus("error");
      }
    }

    void load();
    return () => {
      cancelled = true;
      previewRef.current?.dispose();
      previewRef.current = null;
    };
    // Deps use the resolved primitives (not the objects) so a printer status
    // poll that returns an equal-valued build volume/color doesn't
    // needlessly re-fetch and re-init the preview.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileId, resolvedBuildVolume.x, resolvedBuildVolume.y, resolvedBuildVolume.z, resolvedExtrusionColor]);

  useEffect(() => {
    const preview = previewRef.current;
    if (!preview || status !== "ready") return;
    preview.endLayer = layer;
    preview.render();
  }, [layer, status]);

  // Follow the container (flex-1 / h-full in the dock), not a fixed aspect.
  useEffect(() => {
    const canvas = canvasRef.current;
    const box = canvas?.parentElement;
    if (!canvas || !box || typeof ResizeObserver === "undefined") return;
    let raf = 0;
    const apply = () => {
      raf = 0;
      const preview = previewRef.current;
      const w = box.clientWidth;
      const h = box.clientHeight;
      if (!preview || w < 2 || h < 2) return;
      preview.resize?.();
      preview.render();
    };
    const ro = new ResizeObserver(() => {
      if (!raf) raf = requestAnimationFrame(apply);
    });
    ro.observe(box);
    return () => {
      ro.disconnect();
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  const currentHeight = previewRef.current?.layers[layer - 1]?.height ?? null;

  if (status === "error") {
    return <p className="py-8 text-center text-sm text-muted-foreground">Couldn't load the g-code preview.</p>;
  }

  if (status === "too-large") {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        Too large to preview in the browser.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="aspect-video w-full overflow-hidden rounded-lg border border-border bg-muted">
        <canvas ref={canvasRef} className="h-full w-full" />
      </div>
      {status === "loading" ? (
        <p className="text-center text-sm text-muted-foreground">Loading preview…</p>
      ) : (
        <div className="space-y-1">
          <input
            type="range"
            className="w-full"
            min={1}
            max={Math.max(layerCount, 1)}
            step={1}
            value={layer}
            onChange={(event) => setLayer(Number(event.target.value))}
            aria-label="Layer"
          />
          <p className="text-xs text-muted-foreground">
            Layer {layer} / {layerCount}
            {currentHeight !== null ? ` · Z ${currentHeight.toFixed(2)} mm` : ""}
          </p>
        </div>
      )}
    </div>
  );
}

export default GcodePreview;
