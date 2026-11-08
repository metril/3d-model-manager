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
import { layerZHeights } from "@/lib/gcodeLayerZ";
import { isTooLarge, TOO_LARGE_TO_PREVIEW_BYTES } from "@/lib/gcodeLimits";
import { useTranslation } from "react-i18next";

const DEFAULT_BUILD_VOLUME = { x: 256, y: 256, z: 256 };

/** GET /api/files/{id}/download?member=gcode (R10-B) — the backend
 * extracts a `.gcode.3mf`'s embedded plate gcode; a bare `.gcode` file
 * would just stream itself, though `PlatePanel` only mounts this for
 * sliced files today. */
function gcodeDownloadUrl(fileId: number, plate?: number): string {
  return `/api/files/${fileId}/download?member=gcode${plate !== undefined ? `&plate=${plate}` : ""}`;
}

/** Above this, don't even try to load the gcode text into the browser
 * (review finding 1): `gcode-preview` needs the whole body as one string,
 * and a huge sliced-project plate can be hundreds of MB -- reading
 * `response.text()` on that risks hanging/crashing the tab. */

export function GcodePreview({
  fileId,
  plate,
  buildVolume,
  extrusionColor,
}: {
  fileId: number;
  /** Sliced plate index; omit to let the backend pick the first plate present. */
  plate?: number;
  buildVolume?: { x: number; y: number; z: number };
  extrusionColor?: string;
}) {
  const { t } = useTranslation();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const previewRef = useRef<WebGLPreview | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error" | "too-large">("loading");
  const [layerCount, setLayerCount] = useState(0);
  const [layer, setLayer] = useState(1);
  const layerZRef = useRef<(number | null)[]>([]);

  const printers = usePrinters();
  const firstPrinterId = printers.data?.[0]?.id;
  const printerStatus = usePrinterStatus(firstPrinterId ?? -1, { enabled: firstPrinterId !== undefined });

  const resolvedBuildVolume = buildVolume ?? printers.data?.[0]?.build_volume_mm ?? DEFAULT_BUILD_VOLUME;
  // Mirrors `ViewerMorePanel.tsx`'s `AmsSync`: first tray reporting a
  // truthy `color`, or undefined when none does.
  const resolvedExtrusionColor =
    extrusionColor ?? (printerStatus.data?.trays ?? []).find((tray) => tray.color)?.color ?? undefined;

  // Latest colour/volume are read at init time via refs so a tray colour
  // change never re-fetches or re-parses the file.
  const colorRef = useRef(resolvedExtrusionColor);
  colorRef.current = resolvedExtrusionColor;
  const volumeRef = useRef(resolvedBuildVolume);
  volumeRef.current = resolvedBuildVolume;

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    async function load() {
      setStatus("loading");
      try {
        const [{ init }, response] = await Promise.all([
          import("gcode-preview"),
          fetch(gcodeDownloadUrl(fileId, plate), { credentials: "include", signal: controller.signal }),
        ]);
        if (!response.ok) throw new Error(`gcode download failed: ${response.status}`);

        const contentLength = Number(response.headers.get("content-length"));
        if (Number.isFinite(contentLength) && contentLength > TOO_LARGE_TO_PREVIEW_BYTES) {
          controller.abort();
          if (!cancelled) setStatus("too-large");
          return;
        }

        const text = await response.text();
        if (cancelled || !canvasRef.current) return;
        // Chunked responses carry no content-length; guard on the real size.
        if (isTooLarge(text.length)) {
          setStatus("too-large");
          return;
        }

        const preview = init({
          canvas: canvasRef.current,
          buildVolume: volumeRef.current,
          ...(colorRef.current ? { extrusionColor: colorRef.current } : {}),
        });
        preview.processGCode(text);
        preview.render();
        previewRef.current = preview;
        layerZRef.current = layerZHeights(preview.layers);

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
      controller.abort();
      previewRef.current?.dispose();
      previewRef.current = null;
    };
    // Colour is applied by the effect below without re-parsing.
  }, [fileId, plate, resolvedBuildVolume.x, resolvedBuildVolume.y, resolvedBuildVolume.z]);

  useEffect(() => {
    const preview = previewRef.current;
    if (!preview || status !== "ready" || !resolvedExtrusionColor) return;
    preview.extrusionColor = resolvedExtrusionColor;
    preview.render();
  }, [resolvedExtrusionColor, status]);

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

  const currentHeight = layerZRef.current[layer - 1] ?? null;

  if (status === "error") {
    return <p className="py-8 text-center text-sm text-muted-foreground">{t("viewer.gcode.error")}</p>;
  }

  if (status === "too-large") {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        {t("viewer.gcode.tooLarge")}
      </p>
    );
  }

  return (
    <div className="space-y-2">
      <div className="aspect-video w-full overflow-hidden rounded-lg border border-border bg-muted">
        <canvas ref={canvasRef} className="h-full w-full" />
      </div>
      {status === "loading" ? (
        <p className="text-center text-sm text-muted-foreground">{t("viewer.gcode.loading")}</p>
      ) : (
        <div className="flex items-center gap-3">
          <input
            type="range"
            className="min-w-0 flex-1"
            min={1}
            max={Math.max(layerCount, 1)}
            step={1}
            value={layer}
            onChange={(event) => setLayer(Number(event.target.value))}
            aria-label={t("viewer.gcode.layer")}
          />
          <p className="shrink-0 whitespace-nowrap text-xs text-muted-foreground">
            {t("viewer.gcode.layerOf", { layer, total: layerCount })}
            {currentHeight !== null ? ` · Z ${currentHeight.toFixed(2)} mm` : ""}
          </p>
        </div>
      )}
    </div>
  );
}

export default GcodePreview;
