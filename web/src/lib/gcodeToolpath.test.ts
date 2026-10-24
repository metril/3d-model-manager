import { describe, expect, it } from "vitest";
import {
  filterLayersByType,
  parseDuration,
  parseGcodeToolpath,
  rekeyLayers,
  ToolpathType,
} from "./gcodeToolpath";

import sample from "../test/fixtures/sample.gcode?raw";

const types = (p: ReturnType<typeof parseGcodeToolpath>) => {
  const s = new Set<number>();
  for (const l of p.layers) for (let i = 3; i < l.paths.length; i += 8) s.add(l.paths[i]);
  return s;
};

describe("parseGcodeToolpath", () => {
  const p = parseGcodeToolpath(sample);

  it("counts layers from CHANGE_LAYER markers", () => {
    expect(p.layers.length).toBeGreaterThanOrEqual(8);
    expect(p.stats.layers).toBe(p.layers.length);
    expect(p.layers[0].z).toBeCloseTo(0.2);
    expect(p.layers[1].z).toBeCloseTo(0.4);
  });

  it("maps FEATURE comments to feature types", () => {
    const t = types(p);
    for (const f of [ToolpathType.wall, ToolpathType.sparseInfill, ToolpathType.solidInfill, ToolpathType.support, ToolpathType.skirt]) {
      expect(t.has(f)).toBe(true);
    }
  });

  it("separates extrusion from travel", () => {
    expect(p.segmentCount).toBeGreaterThan(0);
    expect(p.travelCount).toBeGreaterThan(0);
    expect(p.stats.moves).toBe(p.segmentCount + p.travelCount);
    expect(types(p).has(ToolpathType.travel)).toBe(true);
    expect(p.stats.extrusionLength).toBeGreaterThan(0);
  });

  it("computes bounds from extruding moves only", () => {
    expect(p.bounds).not.toBeNull();
    expect(p.bounds!.min[0]).toBeGreaterThanOrEqual(20);
    expect(p.bounds!.max[2]).toBeCloseTo(2.0);
  });

  it("tracks tool changes and speed", () => {
    expect(p.stats.tools).toEqual([0, 1]);
    expect(p.stats.speedRange![0]).toBeLessThan(p.stats.speedRange![1]);
  });

  it("reads the estimated time comment", () => {
    expect(p.stats.estimatedSeconds).toBe(3723);
    expect(parseDuration("1d 2h")).toBe(93600);
    expect(parseDuration("nothing")).toBeNull();
  });

  it("handles absolute extrusion and Z-inferred layers", () => {
    const g = "M82\nG92 E0\nG1 X0 Y0 Z0.2 F1200\nG1 X10 Y0 E1\nG1 X10 Y10 E2\nG1 Z0.4\nG1 X0 Y10 E3\nG1 X0 Y0 E4\n";
    const r = parseGcodeToolpath(g);
    expect(r.layers.length).toBe(2);
    expect(r.stats.extrusionLength).toBeCloseTo(4);
  });

  it("returns an empty result for empty input", () => {
    const r = parseGcodeToolpath("");
    expect(r.layers).toEqual([]);
    expect(r.bounds).toBeNull();
  });
});

describe("filter / rekey", () => {
  const p = parseGcodeToolpath(sample);
  it("drops hidden types but keeps every layer", () => {
    const out = filterLayersByType(p.layers, new Set([ToolpathType.support]));
    expect(out.length).toBe(p.layers.length);
    const t = new Set<number>();
    for (const l of out) for (let i = 3; i < l.paths.length; i += 8) t.add(l.paths[i]);
    expect(t.has(ToolpathType.support)).toBe(false);
    expect(out[0].speeds.length).toBe(out[0].widths.length);
  });
  it("rekeys to 1-based tool / bins, keeping travel at 0", () => {
    const tool = rekeyLayers(p.layers, "tool");
    const speed = rekeyLayers(p.layers, "speed", p.stats.speedRange);
    const layer = rekeyLayers(p.layers, "layer");
    const keys = (ls: typeof tool) => {
      const s = new Set<number>();
      for (const l of ls) for (let i = 3; i < l.paths.length; i += 8) s.add(l.paths[i]);
      return s;
    };
    expect(keys(tool)).toEqual(new Set([0, 1, 2]));
    expect(Math.max(...keys(speed))).toBeLessThanOrEqual(11);
    expect(Math.max(...keys(layer))).toBe(11);
  });
});

describe("performance", () => {
  it("parses a ~5 MB synthetic file", () => {
    const lines = ["M83"];
    for (let l = 0; l < 400; l++) {
      lines.push("; CHANGE_LAYER", `; Z_HEIGHT: ${(l + 1) * 0.2}`, "; FEATURE: Sparse infill");
      for (let i = 0; i < 2000; i++) lines.push(`G1 X${(i % 200) + 0.123} Y${(i % 190) + 0.456} E0.01234 F6000`);
    }
    const text = lines.join("\n");
    expect(text.length).toBeGreaterThan(4_500_000);
    const r = parseGcodeToolpath(text);
    expect(r.layers.length).toBe(400);
  });
});
