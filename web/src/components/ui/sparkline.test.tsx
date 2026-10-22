import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Sparkline } from "@/components/ui/sparkline";

describe("Sparkline", () => {
  it("renders a polyline with points normalised to the given height", () => {
    const { container } = render(<Sparkline values={[0, 5, 10]} height={24} width={96} aria-label="trend" />);
    const polyline = container.querySelector("polyline");
    expect(polyline).not.toBeNull();
    const points = polyline!.getAttribute("points")!;
    const coords = points.trim().split(" ").map((pair) => pair.split(",").map(Number));
    // min value (0) maps to the bottom (height), max value (10) to the top (0).
    expect(coords[0][1]).toBeCloseTo(24);
    expect(coords[2][1]).toBeCloseTo(0);
  });

  it("renders a flat horizontal line for fewer than 2 points", () => {
    const { container } = render(<Sparkline values={[]} height={24} width={96} aria-label="empty" />);
    const polyline = container.querySelector("polyline");
    expect(polyline).not.toBeNull();
    const points = polyline!.getAttribute("points")!;
    const coords = points.trim().split(" ").map((pair) => pair.split(",").map(Number));
    const ys = coords.map((c) => c[1]);
    expect(new Set(ys).size).toBe(1);
  });

  it("carries the given aria-label on the svg for accessibility", () => {
    const { container } = render(<Sparkline values={[1, 2, 3]} aria-label="recent print durations" />);
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("aria-label")).toBe("recent print durations");
  });

  it("applies the tone's stroke color when a tone is given", () => {
    const { container } = render(<Sparkline values={[1, 2, 3]} tone="failed" aria-label="trend" />);
    const polyline = container.querySelector("polyline");
    expect(polyline?.getAttribute("stroke")).toContain("state-failed");
  });
});
