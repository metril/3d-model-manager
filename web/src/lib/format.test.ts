import { beforeEach, describe, expect, it } from "vitest";

import { formatCost, humanizeBytes, humanizeDuration, setCurrency } from "@/lib/format";

describe("humanizeDuration", () => {
  it("shows hours and minutes for durations over an hour", () => {
    expect(humanizeDuration(5400)).toBe("1h 30m");
  });

  it("shows minutes only for durations under an hour", () => {
    expect(humanizeDuration(2700)).toBe("45m");
  });

  it("shows '<1m' for durations under a minute", () => {
    expect(humanizeDuration(30)).toBe("<1m");
    expect(humanizeDuration(0)).toBe("<1m");
  });

  it("shows '<1m' for non-finite input instead of throwing", () => {
    expect(humanizeDuration(Number.NaN)).toBe("<1m");
  });
});

describe("humanizeBytes", () => {
  it("handles plain cases", () => {
    expect(humanizeBytes(0)).toBe("0 B");
    expect(humanizeBytes(1536)).toBe("1.5 KB");
    expect(humanizeBytes(10 * 1024)).toBe("10 KB");
  });
  it("never prints 1024 of a unit", () => {
    expect(humanizeBytes(1024 * 1024 - 1)).toBe("1.0 MB");
    expect(humanizeBytes(1024 * 1024 * 1024 - 1)).toBe("1.0 GB");
  });
  it("rejects bad input", () => {
    expect(humanizeBytes(-1)).toBe("—");
    expect(humanizeBytes(Number.NaN)).toBe("—");
  });
});

describe("formatCost", () => {
  beforeEach(() => setCurrency(""));
  it("formats plain numbers with 2 decimals", () => {
    expect(formatCost(1234.5)).toBe("1,234.50");
    expect(formatCost(0.005)).toBe("0.01");
  });
  it("normalises negative zero", () => {
    expect(formatCost(-0)).toBe("0.00");
    expect(formatCost(-0.001)).toBe("0.00");
  });
  it("shows -- when unknown", () => {
    expect(formatCost(null)).toBe("--");
    expect(formatCost(undefined)).toBe("--");
  });
  it("uses currency style when set; bad code falls back", () => {
    setCurrency("usd");
    expect(formatCost(12.3)).toBe("$12.30");
    setCurrency("not-a-code");
    expect(formatCost(12.3)).toBe("12.30");
  });
});
