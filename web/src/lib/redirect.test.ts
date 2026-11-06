import { describe, expect, it } from "vitest";

import { loginUrlFor, safeRedirect } from "@/lib/redirect";

describe("safeRedirect", () => {
  it("allows same-origin paths", () => {
    expect(safeRedirect("/library?x=1")).toBe("/library?x=1");
    expect(safeRedirect("/")).toBe("/");
  });
  it("rejects external, protocol-relative, backslash and login targets", () => {
    for (const bad of ["https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)", "library", "/login", "/login?x=1", 5, null, undefined]) {
      expect(safeRedirect(bad)).toBeNull();
    }
  });
});

describe("loginUrlFor", () => {
  it("carries a safe return path only", () => {
    expect(loginUrlFor("/models/a b?x=1")).toBe(`/login?redirect=${encodeURIComponent("/models/a b?x=1")}`);
    expect(loginUrlFor("/")).toBe("/login");
    expect(loginUrlFor("//evil.example")).toBe("/login");
  });
});
