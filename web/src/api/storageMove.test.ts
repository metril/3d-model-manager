import { describe, expect, it } from "vitest";

import { canMoveFolder } from "@/api/storageMove";

describe("canMoveFolder", () => {
  const rev = "m/rev-001_x";
  it("allows moves inside the same revision", () => {
    expect(canMoveFolder(`${rev}/a`, `${rev}/b`)).toBe(true);
    expect(canMoveFolder(`${rev}/a/x`, rev)).toBe(true);
  });
  it("rejects no-ops, self/descendant, other revisions and shallow sources", () => {
    expect(canMoveFolder(`${rev}/a`, rev)).toBe(false);
    expect(canMoveFolder(`${rev}/a`, `${rev}/a`)).toBe(false);
    expect(canMoveFolder(`${rev}/a`, `${rev}/a/sub`)).toBe(false);
    expect(canMoveFolder(`${rev}/a`, "m/rev-002_y")).toBe(false);
    expect(canMoveFolder(rev, rev)).toBe(false);
  });
});
