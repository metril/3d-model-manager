import { describe, expect, it, vi } from "vitest";

// jsdom has no WebGL: stub only the renderer so the REAL gcode-preview parser runs.
vi.mock("three", async (orig) => ({
  ...(await orig<typeof import("three")>()),
  WebGLRenderer: class {
    domElement = document.createElement("canvas");
    setPixelRatio() {}
    setSize() {}
    setClearColor() {}
    render() {}
    dispose() {}
  },
}));

import { init } from "gcode-preview";

import { layerZHeights } from "@/lib/gcodeLayerZ";

const GCODE = [
  "G28",
  "G1 Z0.2 F600", // layer 1 Z move
  "G1 X10 Y10 E1", // opens layer 1 @0.2
  "G1 X20 Y10 E2",
  "G1 Z0.6", // z-hop up
  "G1 X30 Y30", // travel
  "G1 Z0.4", // land on layer 2 -- Z move filed under layer 1
  "G1 X40 Y40 E3", // opens layer 2 @0.4
  "G1 Z0.6", // hop
  "G1 Z0.6",
  "G1 X50 Y50 E4", // opens layer 3 @0.6
  "G1 Z0.8",
  "G1 X60 Y60", // travel only, no extrusion
  "G1 Z1.0",
  "G1 X70 Y70 E5", // opens layer 4 @1.0
].join("\n");

describe("layerZHeights", () => {
  it("samples Z at each layer's first extrusion using the real parser (preamble is index 0)", () => {
    const preview = init({ canvas: document.createElement("canvas") });
    preview.processGCode(GCODE);

    const zs = layerZHeights(preview.layers);

    expect(zs).toEqual([null, 0.2, 0.4, 0.6, 1.0]);
    // The lib's own Layer.height is thickness, not Z -- the bug being fixed.
    expect(preview.layers[3].height).not.toBe(0.6);
    preview.dispose();
  });

  it("returns null for a layer with no extruding XY move", () => {
    expect(layerZHeights([{ commands: [{ params: { z: 5 } }] }])).toEqual([null]);
  });
});
