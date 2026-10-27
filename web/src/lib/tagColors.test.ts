import { describe, expect, it } from "vitest";

import {
  TAG_COLORS,
  TAG_PALETTE,
  contrastRatio,
  isHexColor,
  normalizeHex,
  readableTextColor,
  tagChipStyle,
  tagColorClass,
  tagSwatchClass,
} from "@/lib/tagColors";

describe("tagColorClass (category palette keys)", () => {
  it("returns a Tailwind class string for a known color", () => {
    expect(tagColorClass("teal")).toMatch(/bg-teal-100/);
  });

  it("returns undefined for no color", () => {
    expect(tagColorClass(null)).toBeUndefined();
    expect(tagColorClass(undefined)).toBeUndefined();
  });
});

describe("tagSwatchClass", () => {
  it("returns a distinct class for every palette key", () => {
    const classes = new Set(TAG_COLORS.map(tagSwatchClass));
    expect(classes.size).toBe(TAG_COLORS.length);
  });
});

describe("hex helpers", () => {
  it("palette is 10 distinct valid hex values", () => {
    expect(new Set(TAG_PALETTE.map((p) => p.hex)).size).toBe(10);
    expect(TAG_PALETTE.every((p) => isHexColor(p.hex))).toBe(true);
  });

  it("normalizes shorthand and rejects junk", () => {
    expect(normalizeHex("#ABC")).toBe("#aabbcc");
    expect(normalizeHex("12ef90")).toBe("#12ef90");
    expect(normalizeHex("nope")).toBeNull();
  });
});

describe("contrast", () => {
  it("black/white are maximally contrasting", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 0);
  });

  it("picks black text on light fills and white on dark fills", () => {
    expect(readableTextColor("#fde047")).toBe("#000000");
    expect(readableTextColor("#1e3a8a")).toBe("#ffffff");
  });

  it("always achieves at least 4.5:1 on every palette swatch", () => {
    for (const { hex } of TAG_PALETTE) {
      expect(contrastRatio(hex, readableTextColor(hex))).toBeGreaterThanOrEqual(4.5);
    }
  });
});

describe("tagChipStyle", () => {
  it("tints bg/border with color-mix and keeps text theme-aware", () => {
    const style = tagChipStyle("#ef4444");
    expect(style?.backgroundColor).toContain("color-mix");
    expect(style?.borderColor).toContain("#ef4444");
    expect(style?.color).toContain("var(--foreground)");
  });

  it("returns undefined for uncolored / invalid colors", () => {
    expect(tagChipStyle(null)).toBeUndefined();
    expect(tagChipStyle("red")).toBeUndefined();
  });
});
