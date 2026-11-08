/** Structural subset of `gcode-preview`'s Layer/GCodeCommand (types aren't exported). */
export interface PreviewLayer {
  commands: { gcode?: string; params?: { x?: number; y?: number; z?: number; e?: number } }[];
}

const MOVE = /^g0?[0-3]$/i;

/**
 * Real Z (mm) of each layer, aligned with `preview.layers` (index 0 is the
 * lib's preamble). `Layer.height` is the layer *thickness*, not its Z, and a
 * Z move before a layer's first extrusion is filed under the previous layer,
 * so replay every command with a running Z and sample it at each layer's first
 * extruding XY move (the move that opened the layer). Null when a layer has none.
 */
export function layerZHeights(layers: readonly PreviewLayer[]): (number | null)[] {
  let z = 0;
  return layers.map((layer) => {
    let at: number | null = null;
    for (const { gcode, params } of layer.commands) {
      // Only real moves: `M201 X20000 Y20000 Z1500 E5000` also carries params.
      if (!params || !MOVE.test(gcode ?? "")) continue;
      if (params.z != null) z = params.z;
      if (at === null && (params.e ?? 0) > 0 && (params.x != null || params.y != null)) at = z;
    }
    return at;
  });
}

/** Slicer layer count from raw g-code comments (Z_HEIGHT, else CHANGE_LAYER); 0 if none. */
export function countSliceLayers(text: string): number {
  const z = text.match(/^;\s*Z_HEIGHT:/gm)?.length ?? 0;
  return z > 0 ? z : (text.match(/^;\s*CHANGE_LAYER\b/gm)?.length ?? 0);
}

/**
 * Slider position -> slicer layer number. `positions` is `preview.layers.length`
 * (preamble, possibly a purge line, then the slicer's layers). Offset falls back
 * to 1 (preamble only) when the file has no layer comments.
 */
export function sliceLayerLabel(
  position: number,
  positions: number,
  sliceLayers: number,
): { layer: number; total: number } {
  const offset = sliceLayers > 0 ? positions - sliceLayers : 1;
  return { layer: position - offset, total: sliceLayers > 0 ? sliceLayers : positions - 1 };
}
