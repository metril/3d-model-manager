import { describe, expect, it } from "vitest";

import { toneForGcode, toneForResult } from "@/lib/printerState";

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

  it("falls back to idle for an unknown/empty online gcode_state", () => {
    expect(toneForGcode(true, null).tone).toBe("idle");
    expect(toneForGcode(true, "").tone).toBe("idle");
    expect(toneForGcode(true, "UNKNOWN").tone).toBe("idle");
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
