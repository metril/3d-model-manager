import { describe, expect, it } from "vitest";

import { isStreamStalled } from "@/components/print/CameraTile";
import { withSeq } from "@/components/printer/MqttDebugModal";
import { isTooLarge } from "@/components/viewer/GcodePreview";

describe("w3 preview/camera helpers", () => {
  it("withSeq gives unique keys to same-ts same-topic entries", () => {
    const e = { ts: 1, dir: "in", topic: "t", payload: {} };
    const rows = [...withSeq([e, e], 0), ...withSeq([e], 2)];
    expect(new Set(rows.map((r) => r.seq)).size).toBe(3);
  });
  it("stall only when no frame decoded past the window", () => {
    expect(isStreamStalled(0, 6000, 5000)).toBe(true);
    expect(isStreamStalled(0, 4000, 5000)).toBe(false);
    expect(isStreamStalled(640, 60000, 5000)).toBe(false);
  });
  it("isTooLarge uses text length", () => {
    expect(isTooLarge(150 * 1024 * 1024 + 1)).toBe(true);
    expect(isTooLarge(1000)).toBe(false);
  });
});
