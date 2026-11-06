import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { StatsDashboard } from "@/api/stats";
import { Filament, Heatmap } from "@/components/stats/widgets";

const base = {
  days: 30,
  success_rate: { total: 0, completed: 0, failed: 0, canceled: 0, stopped: 0, success_rate: null },
  time_accuracy: { count: 0, mean_delta_pct: null, mean_abs_delta_pct: null, distribution: [], points: [] },
  failures: { total_failed: 0, by_error_code: [], by_hms: [], by_printer: [], by_material: [] },
  records: { longest_print: null, most_filament: null, busiest_day: null },
  printers: [],
} as unknown as StatsDashboard;

describe("stats widgets", () => {
  it("heatmap labels weekdays via Intl and no longer claims UTC", () => {
    const counts = Array.from({ length: 7 }, () => Array<number>(24).fill(0));
    counts[2][14] = 3;
    const { container } = render(<Heatmap d={{ ...base, heatmap: { counts, max: 3 } }} />);
    const monday = new Intl.DateTimeFormat(undefined, { weekday: "short", timeZone: "UTC" }).format(
      new Date(Date.UTC(2024, 0, 1)),
    );
    expect(screen.getByText(monday)).toBeInTheDocument();
    const cell = container.querySelector('rect[data-count="3"] title');
    expect(cell?.textContent).toContain("14:00");
    expect(cell?.textContent).not.toContain("UTC");
  });

  it("filament chart groups the tail into a translated Other series", () => {
    const rows = ["A", "B", "C", "D", "E", "F", "G"].map((m, i) => ({
      week: "2026-10-26",
      material: m,
      grams: 100 - i,
    }));
    const { container } = render(
      <Filament d={{ ...base, heatmap: { counts: [], max: 0 }, filament_trends: rows }} />,
    );
    expect(container.querySelector('[data-series="other"]')).not.toBeNull();
    expect(screen.getAllByText("Other").length).toBeGreaterThan(0);
  });
});
