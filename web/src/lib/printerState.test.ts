import { describe, expect, it } from "vitest";

import { PRINTER_MODELS, modelKey, printerImage, toneForGcode, toneForResult } from "@/lib/printerState";

describe("toneForGcode (overview state tone, Task 3)", () => {
  it("maps RUNNING/PREPARE to printing", () => {
    expect(toneForGcode(true, "RUNNING").tone).toBe("printing");
    expect(toneForGcode(true, "PREPARE").tone).toBe("printing");
  });

  it("maps PAUSE to paused", () => {
    expect(toneForGcode(true, "PAUSE").tone).toBe("paused");
  });

  it("maps FINISH to finished", () => {
    expect(toneForGcode(true, "FINISH").tone).toBe("finished");
  });

  it("maps FAILED to failed", () => {
    expect(toneForGcode(true, "FAILED").tone).toBe("failed");
  });

  it("maps IDLE to idle", () => {
    expect(toneForGcode(true, "IDLE").tone).toBe("idle");
  });

  it("maps offline (online=false) to offline regardless of gcode_state", () => {
    expect(toneForGcode(false, "RUNNING").tone).toBe("offline");
    expect(toneForGcode(false, null).tone).toBe("offline");
  });

  it("returns a human label alongside the tone", () => {
    const { label } = toneForGcode(true, "RUNNING");
    expect(typeof label).toBe("string");
    expect(label.length).toBeGreaterThan(0);
  });

  it("shows no data (not idle) for an unknown/empty gcode_state", () => {
    for (const g of [null, "", "UNKNOWN"]) {
      expect(toneForGcode(true, g)).toEqual({ tone: "offline", label: "No data" });
    }
  });

  it("labels an offline printer Offline", () => {
    expect(toneForGcode(false, null)).toEqual({ tone: "offline", label: "Offline" });
  });
});

describe("toneForResult (archive result tone, Task 3)", () => {
  it("maps completed to finished", () => {
    expect(toneForResult("completed")).toBe("finished");
  });

  it("maps failed to failed", () => {
    expect(toneForResult("failed")).toBe("failed");
  });

  it("maps canceled and stopped to idle", () => {
    expect(toneForResult("canceled")).toBe("idle");
    expect(toneForResult("stopped")).toBe("idle");
  });
});

describe("modelKey / printerImage", () => {
  it("normalises names", () => {
    expect(modelKey("A1 mini")).toBe("a1mini");
    expect(modelKey("Bambu Lab X1-Carbon")).toBe("x1c");
    expect(modelKey("H2D Pro")).toBe("h2dpro");
    expect(modelKey("p2s")).toBe("p2s");
  });
  it("falls back to serial prefix then default", () => {
    expect(modelKey(null, "01P00A123")).toBe("p1s");
    expect(modelKey("mystery", "ZZZ")).toBe("default");
    expect(modelKey(undefined)).toBe("default");
  });
  it("maps aliases to shared images", () => {
    expect(printerImage("h2s")).toBe("/img/printers/h2d.png");
    expect(printerImage("p2s")).toBe("/img/printers/p1s.png");
    expect(printerImage("default")).toBe("/img/printers/default.png");
  });
  it("every listed model resolves to a non-default key", () => {
    for (const g of PRINTER_MODELS) for (const o of g.models) expect(modelKey(o.value)).not.toBe("default");
    expect(modelKey("X1 Carbon")).toBe("x1c");
  });
});

describe("PRINTER_MODELS stored values vs slicer names", () => {
  const SLICER: Record<string, string> = {
    A1: "Bambu Lab A1", "A1 mini": "Bambu Lab A1 mini", A1F: "Bambu Lab A1F", A2L: "Bambu Lab A2L",
    H2C: "Bambu Lab H2C", H2D: "Bambu Lab H2D", "H2D Pro": "Bambu Lab H2D Pro", H2S: "Bambu Lab H2S",
    O1C: "Bambu Lab O1C", O1E: "Bambu Lab O1E", O1S: "Bambu Lab O1S",
    P1P: "Bambu Lab P1P", P1S: "Bambu Lab P1S", P2S: "Bambu Lab P2S",
    "X1 Carbon": "Bambu Lab X1 Carbon", X1E: "Bambu Lab X1E", X2D: "Bambu Lab X2D",
  };
  it("each value is a substring of its slicer printer name", () => {
    for (const g of PRINTER_MODELS)
      for (const o of g.models) {
        expect(SLICER[o.value], o.value).toBeDefined();
        expect(SLICER[o.value].toLowerCase()).toContain(o.value.toLowerCase());
      }
  });
});
