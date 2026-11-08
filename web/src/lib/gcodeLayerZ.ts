/** Structural subset of `gcode-preview`'s Layer/GCodeCommand (types aren't exported). */
export interface PreviewLayer {
  commands: { params?: { x?: number; y?: number; z?: number; e?: number } }[];
}

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
    for (const { params } of layer.commands) {
      if (!params) continue;
      if (params.z != null) z = params.z;
      if (at === null && (params.e ?? 0) > 0 && (params.x != null || params.y != null)) at = z;
    }
    return at;
  });
}
