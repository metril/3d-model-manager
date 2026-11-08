import { describe, expect, it } from "vitest";

import { paletteFilter } from "@/components/shell/paletteFilter";

describe("paletteFilter", () => {
  it("prefers word prefixes and drops loose subsequences", () => {
    expect(paletteFilter("Plugs", "plu")).toBe(1);
    expect(paletteFilter("Duplicates", "pla")).toBe(0);
    expect(paletteFilter("Plates", "pla")).toBeGreaterThan(0);
    expect(paletteFilter("anything", "")).toBe(1);
  });
});
