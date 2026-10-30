import { describe, expect, it } from "vitest";

import { parseScanUrl } from "@/lib/scanUrl";

describe("parseScanUrl", () => {
  it("parses spool and location URLs and bare paths", () => {
    expect(parseScanUrl("https://tdmm.local/scan/spool/12")).toEqual({ kind: "spool", id: 12 });
    expect(parseScanUrl("http://h:8080/scan/location/3/")).toEqual({ kind: "location", id: 3 });
    expect(parseScanUrl("/scan/spool/7")).toEqual({ kind: "spool", id: 7 });
  });
  it("accepts legacy spool links", () => {
    expect(parseScanUrl("https://h/inventory/spools?spool=9")).toEqual({ kind: "spool", id: 9 });
  });
  it("rejects everything else", () => {
    for (const bad of ["", "hello", "https://h/scan/box/1", "/scan/spool/x", "/scan/spool/1/extra", "https://h/inventory/spools"]) {
      expect(parseScanUrl(bad)).toBeNull();
    }
  });
});
