/**
 * Parses a G-code file into the per-layer form the vendored libvgcode renderer
 * consumes (`src/lib/vendor/toolpathRenderer.js`).
 *
 * This is the piece that does not exist upstream. `three-slicer` renders its own
 * slicing kernel's output and ships no G-code parser at all, so a preview of a
 * *file* -- which is all Bambuddy ever has -- needs the toolpath reconstructed
 * from the text.
 *
 * The renderer's input is one entry per layer:
 *
 *     { z, paths: Float32Array (stride 8), widths: number[] }
 *
 * where each stride-8 record is `x0, y0, z0, type, x1, y1, z1, _` and `type` is
 * 0 for a travel move or a feature index otherwise. Layer height is derived by
 * the renderer from the gaps between consecutive `z` values, so layers must
 * arrive in print order.
 *
 * Deliberately hand-rolled rather than reusing `gcode-preview`'s parser: that
 * one models moves for a line renderer and keeps `;TYPE:` only as an opaque
 * comment string, so the feature classification below -- the thing that makes a
 * preview readable -- would have to be written here anyway.
 */

/**
 * Feature indices the renderer's palette is keyed on. Values are fixed by
 * `TYPE_COLOR` in the vendored module, which took them from libvgcode; changing
 * one silently recolours the preview.
 */
export const ToolpathType = {
  travel: 0,
  wall: 1,
  sparseInfill: 2,
  solidInfill: 3,
  skirt: 4,
  support: 5,
  raft: 6,
  gapFill: 7,
  thinWall: 8,
  bridge: 9,
  ironing: 10,
  primeTower: 11,
} as const;

/**
 * `;TYPE:` values as OrcaSlicer and BambuStudio emit them, lowercased.
 *
 * Both spell several of these differently across versions ("Overhang wall" vs
 * "Overhang perimeter"), and PrusaSlicer-lineage names turn up in third-party
 * files, so the table is deliberately generous. Anything unrecognised falls
 * back to `wall`, which is visually neutral -- better a mis-coloured segment
 * than a missing one, since an unknown type must never drop geometry.
 */
const FEATURE_BY_COMMENT: Record<string, number> = {
  'outer wall': ToolpathType.wall,
  'inner wall': ToolpathType.wall,
  perimeter: ToolpathType.wall,
  'external perimeter': ToolpathType.wall,
  'overhang wall': ToolpathType.bridge,
  'overhang perimeter': ToolpathType.bridge,
  'sparse infill': ToolpathType.sparseInfill,
  'internal infill': ToolpathType.sparseInfill,
  'solid infill': ToolpathType.solidInfill,
  'internal solid infill': ToolpathType.solidInfill,
  'top surface': ToolpathType.solidInfill,
  'top solid infill': ToolpathType.solidInfill,
  'bottom surface': ToolpathType.solidInfill,
  skirt: ToolpathType.skirt,
  'skirt/brim': ToolpathType.skirt,
  brim: ToolpathType.skirt,
  support: ToolpathType.support,
  'support material': ToolpathType.support,
  'support interface': ToolpathType.support,
  'support material interface': ToolpathType.support,
  'support transition': ToolpathType.support,
  raft: ToolpathType.raft,
  'gap fill': ToolpathType.gapFill,
  'gap infill': ToolpathType.gapFill,
  'thin wall': ToolpathType.thinWall,
  // Bambu-only names.
  'floating vertical shell': ToolpathType.solidInfill,
  'internal bridge': ToolpathType.bridge,
  'bottom shell': ToolpathType.solidInfill,
  bridge: ToolpathType.bridge,
  'bridge infill': ToolpathType.bridge,
  'internal bridge infill': ToolpathType.bridge,
  ironing: ToolpathType.ironing,
  'prime tower': ToolpathType.primeTower,
  'wipe tower': ToolpathType.primeTower,
  custom: ToolpathType.wall,
};

/** One layer in the shape `buildSegmentData` expects. */
export interface ToolpathLayer {
  z: number;
  paths: Float32Array;
  widths: number[];
  /** Feed rate in mm/min per record (parallel to `widths`). */
  speeds: number[];
}

export interface ToolpathStats {
  layers: number;
  /** Extruding + travel moves. */
  moves: number;
  /** Total filament pushed, in mm of filament (positive E only). */
  extrusionLength: number;
  /** Slicer-reported print time in seconds, when the file carries it. */
  estimatedSeconds: number | null;
  /** Distinct filament indices seen on extruding moves. */
  tools: number[];
  speedRange: [number, number] | null;
}

export interface ParsedToolpath {
  layers: ToolpathLayer[];
  /** Extruding segments, excluding travels. */
  segmentCount: number;
  travelCount: number;
  /** Nozzle/line width seen in the file, for the renderer's fallback. */
  defaultWidth: number;
  bounds: { min: [number, number, number]; max: [number, number, number] } | null;
  stats: ToolpathStats;
}

const RECORD_STRIDE = 8;
const TAU = Math.PI * 2;
/** Chord flatness for arc interpolation, in mm. Below an extrusion width. */
const ARC_TOLERANCE_MM = 0.02;
/** Ceiling on chords per arc, so a huge radius cannot blow up the buffer. */
const ARC_MAX_CHORDS = 256;
/** Tool numbers above this are slicer sentinels, not filaments. */
const MAX_TOOL = 15;

/** Growable stride-8 record buffer; typed arrays cannot be pushed to. */
class PathBuffer {
  private data = new Float32Array(1024 * RECORD_STRIDE);
  private count = 0;
  readonly widths: number[] = [];
  readonly speeds: number[] = [];

  push(
    x0: number, y0: number, z0: number,
    type: number,
    x1: number, y1: number, z1: number,
    width: number,
    tool: number,
    speed = 0,
  ): void {
    if ((this.count + 1) * RECORD_STRIDE > this.data.length) {
      const grown = new Float32Array(this.data.length * 2);
      grown.set(this.data);
      this.data = grown;
    }
    const o = this.count * RECORD_STRIDE;
    this.data[o] = x0;
    this.data[o + 1] = y0;
    this.data[o + 2] = z0;
    this.data[o + 3] = type;
    this.data[o + 4] = x1;
    this.data[o + 5] = y1;
    this.data[o + 6] = z1;
    // Slot 7 is unread by the renderer, so the active filament rides along in
    // it. That is what lets the viewer offer a filament-coloured view without
    // parsing the file twice: it swaps slot 3 for slot 7 and rebuilds.
    this.data[o + 7] = tool;
    this.count += 1;
    this.widths.push(width);
    this.speeds.push(speed);
  }

  get length(): number {
    return this.count;
  }

  /** Trimmed copy — the renderer walks the whole array, so slack would render. */
  toFloat32Array(): Float32Array {
    return this.data.slice(0, this.count * RECORD_STRIDE);
  }
}

/** Most frequently seen key, or undefined when the tally is empty. */
function modeOf(tally: Map<number, number>): number | undefined {
  let best: number | undefined;
  let bestCount = 0;
  for (const [value, count] of tally) {
    if (count > bestCount) {
      best = value;
      bestCount = count;
    }
  }
  return best;
}

/** "1d 2h 3m 4s" (any subset) to seconds; null when nothing parses. */
export function parseDuration(text: string): number | null {
  let total = 0;
  let found = false;
  for (const m of text.matchAll(/(\d+(?:\.\d+)?)\s*([dhms])\b/gi)) {
    found = true;
    total += Number(m[1]) * { d: 86400, h: 3600, m: 60, s: 1 }[m[2].toLowerCase() as 'd' | 'h' | 'm' | 's'];
  }
  return found ? total : null;
}

/** Reads a named axis out of a `G0`/`G1` line without allocating per token. */
function readAxis(line: string, axis: string): number | undefined {
  const at = line.indexOf(axis);
  if (at < 0) return undefined;
  // Guard against matching inside a comment or a word ("; X marks").
  const value = Number.parseFloat(line.slice(at + 1));
  return Number.isFinite(value) ? value : undefined;
}

/**
 * Parse G-code into per-layer toolpath records.
 *
 * Relative extrusion (`M83`) and absolute (`M82`) are both handled, because
 * Bambu writes relative and plenty of third-party files do not. Anything the
 * parser cannot make sense of is skipped rather than guessed at.
 */
export function parseGcodeToolpath(gcode: string): ParsedToolpath {
  const layers: ToolpathLayer[] = [];

  let x = 0;
  let y = 0;
  let z = 0;
  let e = 0;
  let relativeExtrusion = false;
  let feature: number = ToolpathType.wall;
  let width = 0;
  // Tally of observed widths. The *typical* one is wanted, not the largest: a
  // file's widths range from a 0.09 gap fill to a 1.0 purge line, and taking
  // the max made the fallback wildly too fat.
  const widthTally = new Map<number, number>();
  let segmentCount = 0;
  let travelCount = 0;

  let current = new PathBuffer();
  let currentZ = 0;
  // A file with explicit layer markers is trusted; without them, layers are
  // inferred from the Z at which material is *laid down*.
  let sawLayerMarker = false;
  let pendingZ: number | null = null;
  // Travels share the layer buffer, so "the buffer is empty" is not the same
  // question as "this layer has laid anything down yet" -- and it is the first
  // *extrusion* that fixes a layer's height.
  let layerHasExtrusion = false;
  // Active filament. BambuStudio also emits sentinel tool numbers
  // (T65535 / T65279) around its own bookkeeping; those are not filaments.
  let tool = 0;
  // Suppresses a phantom segment from the origin: the machine's position is
  // unknown until the first move sets it, and drawing from (0,0,0) put a stray
  // line across the bed.
  let hasPosition = false;
  let feed = 0;
  let extrusionLength = 0;
  let estimatedSeconds: number | null = null;
  let minSpeed = Infinity;
  let maxSpeed = -Infinity;
  const tools = new Set<number>();

  let minX = Infinity, minY = Infinity, minZ = Infinity;
  let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;

  const flushLayer = () => {
    if (current.length === 0) return;
    layers.push({ z: currentZ, paths: current.toFloat32Array(), widths: current.widths, speeds: current.speeds });
    current = new PathBuffer();
    layerHasExtrusion = false;
    if (pendingZ !== null) {
      currentZ = pendingZ;
      pendingZ = null;
    }
  };

  /**
   * Record one straight run from the current position, updating bounds. Shared
   * by linear moves and by each chord an arc is flattened into.
   */
  const emit = (nx: number, ny: number, nz: number, extruding: boolean) => {
    if (extruding) {
      if (!sawLayerMarker && layerHasExtrusion && nz !== currentZ) flushLayer();
      if (!layerHasExtrusion) {
        currentZ = nz;
        pendingZ = null;
      }
      layerHasExtrusion = true;

      if (!hasPosition) {
        hasPosition = true;
        x = nx; y = ny; z = nz;
        return;
      }

      current.push(x, y, z, feature, nx, ny, nz, width, tool, feed);
      tools.add(tool);
      if (feed > 0) {
        if (feed < minSpeed) minSpeed = feed;
        if (feed > maxSpeed) maxSpeed = feed;
      }
      segmentCount += 1;
      if (nx < minX) minX = nx;
      if (ny < minY) minY = ny;
      if (nz < minZ) minZ = nz;
      if (nx > maxX) maxX = nx;
      if (ny > maxY) maxY = ny;
      if (nz > maxZ) maxZ = nz;
    } else if (hasPosition) {
      current.push(x, y, z, ToolpathType.travel, nx, ny, nz, 0, tool, feed);
      travelCount += 1;
    }
    x = nx; y = ny; z = nz;
    hasPosition = true;
  };

  for (const rawLine of gcode.split('\n')) {
    const line = rawLine.trim();
    if (line.length === 0) continue;

    if (line.charCodeAt(0) === 59 /* ; */) {
      // Slicer annotations, in either dialect. BambuStudio writes
      // "; FEATURE: Outer wall" and "; CHANGE_LAYER"; OrcaSlicer and the
      // PrusaSlicer lineage write ";TYPE:Outer wall" and ";LAYER_CHANGE".
      // Reading only one of them is why an earlier version of this parser
      // rendered a Bambu file as a single undifferentiated colour.
      const body = line.slice(1).trimStart();
      const colon = body.indexOf(':');
      const key = (colon >= 0 ? body.slice(0, colon) : body).trim().toUpperCase();
      const value = colon >= 0 ? body.slice(colon + 1).trim() : '';

      if (estimatedSeconds === null && /estimated (printing )?time|total estimated time/i.test(body)) {
        estimatedSeconds = parseDuration(body.slice(body.indexOf('=') >= 0 ? body.indexOf('=') + 1 : body.indexOf(':') + 1));
      }

      if (key === 'FEATURE' || key === 'TYPE') {
        feature = FEATURE_BY_COMMENT[value.toLowerCase()] ?? ToolpathType.wall;
      } else if (key === 'LINE_WIDTH' || key === 'WIDTH') {
        const parsed = Number.parseFloat(value);
        if (Number.isFinite(parsed) && parsed > 0) {
          width = parsed;
          widthTally.set(parsed, (widthTally.get(parsed) ?? 0) + 1);
        }
      } else if (key === 'CHANGE_LAYER' || key === 'LAYER_CHANGE') {
        // An explicit marker is authoritative: it is the only thing that
        // distinguishes a real layer change from a travel Z-hop.
        sawLayerMarker = true;
        flushLayer();
      } else if (key === 'Z_HEIGHT' || key === 'Z') {
        const parsed = Number.parseFloat(value);
        if (Number.isFinite(parsed)) pendingZ = parsed;
      }
      continue;
    }

    if (line.startsWith('M83')) {
      relativeExtrusion = true;
      continue;
    }
    if (line.startsWith('M82')) {
      relativeExtrusion = false;
      continue;
    }
    if (line.startsWith('G92')) {
      const resetE = readAxis(line, 'E');
      if (resetE !== undefined) e = resetE;
      continue;
    }
    if (line.charCodeAt(0) === 84 /* T */) {
      // Filament change. Values above the sensible tool range are BambuStudio
      // sentinels around its own bookkeeping (T65535 / T65279), not filaments.
      const picked = Number.parseInt(line.slice(1), 10);
      if (Number.isFinite(picked) && picked >= 0 && picked <= MAX_TOOL) tool = picked;
      continue;
    }

    const isArc = line.startsWith('G2 ') || line.startsWith('G3 ') || line.startsWith('G2') || line.startsWith('G3');
    const isLinear = line.startsWith('G1') || line.startsWith('G0');
    if (!isLinear && !isArc) continue;
    // G20/G21/G28 etc. share the G-prefix; only the four move codes above are
    // handled, and `startsWith('G2')` would otherwise swallow G20/G28.
    if (isArc && !/^G[23](\s|$)/.test(line)) continue;
    if (isLinear && !/^G[01](\s|$)/.test(line)) continue;

    const nx = readAxis(line, 'X') ?? x;
    const ny = readAxis(line, 'Y') ?? y;
    const nz = readAxis(line, 'Z') ?? z;
    const rawE = readAxis(line, 'E');
    const rawF = readAxis(line, 'F');
    if (rawF !== undefined && rawF > 0) feed = rawF;

    let extruded = 0;
    if (rawE !== undefined) {
      extruded = relativeExtrusion ? rawE : rawE - e;
      e = rawE;
    }
    const extruding = extruded > 0;
    if (extruding) extrusionLength += extruded;

    if (isArc) {
      // Arc move in the XY plane (every file seen uses G17, and I/J rather
      // than R). Ignoring these dropped 706 extruding moves out of ~8500 in a
      // single plate -- concentrated on curved walls and tree supports, which
      // is precisely where the preview came out full of holes.
      const i = readAxis(line, 'I') ?? 0;
      const j = readAxis(line, 'J') ?? 0;
      const cx = x + i;
      const cy = y + j;
      const radius = Math.hypot(i, j);

      if (radius > 0) {
        const startAngle = Math.atan2(y - cy, x - cx);
        const endAngle = Math.atan2(ny - cy, nx - cx);
        const clockwise = line.charCodeAt(1) === 50; /* G2 */

        let sweep = endAngle - startAngle;
        if (clockwise) {
          while (sweep >= 0) sweep -= TAU;
          while (sweep < -TAU) sweep += TAU;
        } else {
          while (sweep <= 0) sweep += TAU;
          while (sweep > TAU) sweep -= TAU;
        }
        // A move with no X/Y is a full turn -- BambuStudio's helical travel
        // lift -- and `P` says how many.
        if (nx === x && ny === y) {
          const turns = Math.max(1, Math.round(readAxis(line, 'P') ?? 1));
          sweep = (clockwise ? -TAU : TAU) * turns;
        }

        // Chord count from a flatness tolerance rather than a fixed step, so a
        // 40mm arc is not drawn with the same four chords as a 1mm one.
        const maxStep = 2 * Math.acos(Math.max(-1, Math.min(1, 1 - ARC_TOLERANCE_MM / radius)));
        const steps = Math.max(1, Math.min(ARC_MAX_CHORDS, Math.ceil(Math.abs(sweep) / Math.max(maxStep, 1e-3))));

        for (let step = 1; step <= steps; step += 1) {
          const fraction = step / steps;
          const angle = startAngle + sweep * fraction;
          emit(
            cx + radius * Math.cos(angle),
            cy + radius * Math.sin(angle),
            z + (nz - z) * fraction,
            extruding,
          );
        }
        continue;
      }
      // Degenerate arc (no radius): fall through and treat it as a straight
      // move rather than dropping the geometry.
    }

    if (nx !== x || ny !== y || nz !== z) emit(nx, ny, nz, extruding);
  }

  flushLayer();

  return {
    layers,
    segmentCount,
    travelCount,
    defaultWidth: modeOf(widthTally) ?? 0.42,
    bounds: segmentCount > 0 ? { min: [minX, minY, minZ], max: [maxX, maxY, maxZ] } : null,
    stats: {
      layers: layers.length,
      moves: segmentCount + travelCount,
      extrusionLength,
      estimatedSeconds,
      tools: [...tools].sort((a, b) => a - b),
      speedRange: maxSpeed >= minSpeed ? [minSpeed, maxSpeed] : null,
    },
  };
}

/** Colour modes that re-key records (feature mode keeps the parsed types). */
export type RekeyMode = 'tool' | 'speed' | 'layer';
/** Bins used by speed/layer modes; vType is a Uint8 and ramps have 11 stops. */
export const RAMP_BINS = 11;

/**
 * Re-key a parsed toolpath so each record's *type* is its tool / speed bin /
 * layer bin rather than its feature. Must be a copy: the renderer merges
 * adjacent vertices only when their type matches. Travels keep type 0.
 * Non-travel keys are 1-based (tool+1, bin+1) so they never collide with 0.
 */
export function rekeyLayers(
  layers: ToolpathLayer[],
  mode: RekeyMode,
  speedRange: [number, number] | null = null,
): ToolpathLayer[] {
  const [lo, hi] = speedRange ?? [0, 0];
  const bin = (t: number) => Math.min(RAMP_BINS - 1, Math.max(0, Math.floor(t * RAMP_BINS)));
  return layers.map((layer, li) => {
    const paths = layer.paths.slice();
    const layerKey = bin(layers.length > 1 ? li / (layers.length - 1) : 0) + 1;
    for (let i = 0; i < paths.length; i += RECORD_STRIDE) {
      if (paths[i + 3] === ToolpathType.travel) continue;
      if (mode === 'tool') paths[i + 3] = Math.min(paths[i + 7] + 1, MAX_TOOL);
      else if (mode === 'layer') paths[i + 3] = layerKey;
      else {
        const f = layer.speeds[i / RECORD_STRIDE] ?? 0;
        paths[i + 3] = bin(hi > lo ? (f - lo) / (hi - lo) : 0) + 1;
      }
    }
    return { ...layer, paths };
  });
}

/** Back-compat alias: filament (tool) keying. */
export const layersByFilament = (layers: ToolpathLayer[]): ToolpathLayer[] => rekeyLayers(layers, 'tool');

/**
 * Drop records whose type is hidden, so they never reach the renderer.
 *
 * Hiding has to happen here rather than by recolouring: the shader packs
 * colour into a single float with no alpha channel, so there is no
 * "transparent" to set. Removing the records is also what makes hiding
 * useful -- a hidden support genuinely stops occluding the model behind it.
 */
export function filterLayersByType(layers: ToolpathLayer[], hidden: ReadonlySet<number>): ToolpathLayer[] {
  if (hidden.size === 0) return layers;

  const out: ToolpathLayer[] = [];
  for (const layer of layers) {
    const kept = new PathBuffer();
    for (let i = 0; i < layer.paths.length; i += RECORD_STRIDE) {
      const type = layer.paths[i + 3];
      if (hidden.has(type)) continue;
      kept.push(
        layer.paths[i], layer.paths[i + 1], layer.paths[i + 2], type,
        layer.paths[i + 4], layer.paths[i + 5], layer.paths[i + 6],
        layer.widths[i / RECORD_STRIDE] ?? 0,
        layer.paths[i + 7],
        layer.speeds[i / RECORD_STRIDE] ?? 0,
      );
    }
    // A layer emptied by the filter is still a layer: dropping it would
    // renumber every layer above it and make the range slider lie.
    out.push({ ...layer, paths: kept.toFloat32Array(), widths: kept.widths, speeds: kept.speeds });
  }
  return out;
}
