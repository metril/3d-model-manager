import { describe, expect, it } from "vitest";

import { findScrollParent } from "@/lib/scrollParent";

describe("findScrollParent", () => {
  it("returns the nearest overflow-y auto/scroll ancestor", () => {
    const outer = document.createElement("div");
    outer.style.overflowY = "scroll";
    const inner = document.createElement("div");
    inner.style.overflowY = "auto";
    const child = document.createElement("div");
    outer.append(inner);
    inner.append(child);
    document.body.append(outer);
    expect(findScrollParent(child)).toBe(inner);
    outer.remove();
  });

  it("returns null when nothing scrolls", () => {
    const child = document.createElement("div");
    document.body.append(child);
    expect(findScrollParent(child)).toBeNull();
    child.remove();
  });
});
