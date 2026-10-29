import { describe, expect, it } from "vitest";

import { isUniversal, modelKey, profileInScope, scopeMismatchReason } from "@/lib/profileScope";

describe("modelKey", () => {
  it("maps every spelling of A1 mini to one key", () => {
    expect(modelKey("A1M")).toBe("a1mini");
    expect(modelKey("N1")).toBe("a1mini");
    expect(modelKey("Bambu Lab A1 mini")).toBe("a1mini");
    expect(modelKey("A1 mini")).toBe("a1mini");
    expect(modelKey("Bambu Lab Bambulab A1 mini")).toBe("a1mini");
  });

  it("keeps A1 distinct from A1 mini (key equality, never substring)", () => {
    expect(modelKey("A1")).toBe("a1");
    expect(modelKey("A1")).not.toBe(modelKey("A1 mini"));
  });

  it("returns null for empty input", () => {
    expect(modelKey(null)).toBeNull();
    expect(modelKey("  ")).toBeNull();
  });
});

describe("profileInScope", () => {
  const mini04 = { compat: [{ model_key: "a1mini", nozzle_mm: 0.4 }], printer_model_key: "a1mini", printer_model: "A1 mini" };

  it("treats a profile without model or compat as universal", () => {
    const universal = { compat: null, printer_model_key: null, printer_model: null };
    expect(isUniversal(universal)).toBe(true);
    expect(profileInScope(universal, "a1mini", 0.4)).toBe(true);
  });

  it("matches model key and nozzle", () => {
    expect(profileInScope(mini04, "a1mini", 0.4)).toBe(true);
    expect(profileInScope(mini04, "a1", 0.4)).toBe(false);
    expect(profileInScope(mini04, "a1mini", 0.6)).toBe(false);
  });

  it("applies the 0.01 mm nozzle tolerance and null = any", () => {
    expect(profileInScope(mini04, "a1mini", 0.405)).toBe(true);
    expect(profileInScope(mini04, "a1mini", null)).toBe(true);
    expect(profileInScope({ ...mini04, compat: [{ model_key: "a1mini", nozzle_mm: null }] }, "a1mini", 0.8)).toBe(true);
  });

  it("passes any compat entry (multi-printer process presets)", () => {
    const multi = { ...mini04, compat: [{ model_key: "a1mini", nozzle_mm: 0.4 }, { model_key: "p1s", nozzle_mm: 0.4 }] };
    expect(profileInScope(multi, "p1s", 0.4)).toBe(true);
    expect(profileInScope(multi, "x1carbon", 0.4)).toBe(false);
  });

  it("treats printer_model_key null + compat null as universal (matches backend)", () => {
    const legacy = { compat: null, printer_model_key: null, printer_model: "Bambu Lab A1 mini" };
    expect(isUniversal(legacy)).toBe(true);
    expect(profileInScope(legacy, "a1", 0.4)).toBe(true);
  });

  it("explains a mismatch", () => {
    expect(scopeMismatchReason(mini04, "a1mini", 0.4)).toBeNull();
    expect(scopeMismatchReason(mini04, "a1mini", 0.6)).toBe("for 0.4 mm nozzle");
    expect(scopeMismatchReason(mini04, "p1s", 0.4)).toBe("for A1 mini");
  });
});
