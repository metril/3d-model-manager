import { describe, expect, it } from "vitest";

import { resolveOverlayContainer } from "@/lib/portalContainer";

describe("resolveOverlayContainer", () => {
  const stage = document.createElement("div");
  const popoutBody = document.createElement("body");
  it("uses the stage only while fullscreen", () => {
    expect(resolveOverlayContainer(true, stage, undefined)).toBe(stage);
    expect(resolveOverlayContainer(true, stage, popoutBody)).toBe(stage);
  });
  it("otherwise uses the owning document's container (default body when none)", () => {
    expect(resolveOverlayContainer(false, stage, undefined)).toBeUndefined();
    expect(resolveOverlayContainer(false, stage, popoutBody)).toBe(popoutBody);
  });
});
