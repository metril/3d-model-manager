import { describe, expect, it } from "vitest";

import { isInsecureOrcaSlicerContext } from "@/components/orcaslicer/orcaslicerNav";

describe("isInsecureOrcaSlicerContext", () => {
  it("allows https and localhost, flags plain http elsewhere", () => {
    expect(isInsecureOrcaSlicerContext({ protocol: "https:", hostname: "tdmm.lan" })).toBe(false);
    expect(isInsecureOrcaSlicerContext({ protocol: "http:", hostname: "localhost" })).toBe(false);
    expect(isInsecureOrcaSlicerContext({ protocol: "http:", hostname: "tdmm.lan" })).toBe(true);
  });
});
