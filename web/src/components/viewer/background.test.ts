import { describe, expect, it } from "vitest";

import { resolveBackground } from "@/components/viewer/background";

describe("resolveBackground", () => {
  it("resolves the studio preset regardless of theme or custom color", () => {
    expect(resolveBackground("studio", "#123456", false)).toBe("#9aa5b8");
    expect(resolveBackground("studio", "#123456", true)).toBe("#9aa5b8");
  });

  it("resolves the white preset", () => {
    expect(resolveBackground("white", "#123456", false)).toBe("#ffffff");
  });

  it("resolves the dark preset", () => {
    expect(resolveBackground("dark", "#123456", false)).toBe("#12151c");
  });

  it("resolves the custom preset to the given hex", () => {
    expect(resolveBackground("custom", "#654321", false)).toBe("#654321");
  });

  it("falls back to the studio hex when the custom preset has no color", () => {
    expect(resolveBackground("custom", "", false)).toBe("#9aa5b8");
  });

  it("follows the app theme: dark resolves to the dark neutral", () => {
    expect(resolveBackground("theme", "#000000", true)).toBe("#12151c");
  });

  it("follows the app theme: light resolves to a light neutral", () => {
    expect(resolveBackground("theme", "#000000", false)).toBe("#e6e9ef");
  });
});
