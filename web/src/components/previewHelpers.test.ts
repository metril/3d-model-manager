import { describe, expect, it } from "vitest";

import { framesEqual, isStreamStalled, reconnectDelayMs } from "@/components/print/CameraTile";
import { withSeq } from "@/components/printer/MqttDebugModal";
import { isTooLarge } from "@/components/viewer/GcodePreview";

describe("w3 preview/camera helpers", () => {
  it("withSeq gives unique keys to same-ts same-topic entries", () => {
    const e = { ts: 1, dir: "in", topic: "t", payload: {} };
    const rows = [...withSeq([e, e], 0), ...withSeq([e], 2)];
    expect(new Set(rows.map((r) => r.seq)).size).toBe(3);
  });
  it("stall only when no frame decoded past the window", () => {
    expect(isStreamStalled(0, 6000, 0, 5000)).toBe(true);
    expect(isStreamStalled(0, 4000, 0, 5000)).toBe(false);
    expect(isStreamStalled(640, 60000, 0, 5000)).toBe(false);
  });
  it("isTooLarge uses text length", () => {
    expect(isTooLarge(150 * 1024 * 1024 + 1)).toBe(true);
    expect(isTooLarge(1000)).toBe(false);
  });
});

describe("camera stall helpers", () => {
  it("noisy scene does not stall; identical frames for 20s do", () => {
    expect(isStreamStalled(640, 1e6, 1000, 20_000)).toBe(false);
    expect(isStreamStalled(640, 1e6, 20_001, 20_000)).toBe(true);
    expect(isStreamStalled(0, 21_000, 0, 20_000)).toBe(true);
    expect(framesEqual(new Uint8ClampedArray([1, 2]), new Uint8ClampedArray([1, 3]))).toBe(false);
    expect(framesEqual(new Uint8ClampedArray([1, 2]), new Uint8ClampedArray([1, 2]))).toBe(true);
  });
  it("backoff 5,10,20,40,60 capped", () => {
    expect([0, 1, 2, 3, 4, 5, 9].map(reconnectDelayMs)).toEqual([5000, 10000, 20000, 40000, 60000, 60000, 60000]);
  });
});
