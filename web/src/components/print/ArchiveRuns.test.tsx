import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ArchiveRun } from "@/api/types";
import { ArchiveRuns, RunCountBadge } from "@/components/print/ArchiveRuns";

const { runsMock } = vi.hoisted(() => ({ runsMock: vi.fn() }));
vi.mock("@/api/archives", () => ({ useArchiveRuns: () => ({ data: runsMock() }) }));

const base = {
  archive_id: 1,
  print_job_id: 1,
  printer_id: 1,
  started_at: null,
  finished_at: "2026-10-20T10:00:00Z",
  printed_at: "2026-10-20T10:00:00Z",
  filament_g: 10,
  filament_cost: 1,
  machine_cost: 1,
  wear_cost: null,
  energy_cost: null,
} as const;

beforeEach(() => runsMock.mockReset());

describe("ArchiveRuns", () => {
  it("lists runs and highlights the latest", () => {
    runsMock.mockReturnValue([
      { ...base, id: 2, printer_name: "A1", outcome: "fail", duration_min: 30, total_cost: 2.5, is_latest: true },
      { ...base, id: 1, printer_name: "P1S", outcome: "success", duration_min: 60, total_cost: 3, is_latest: false },
    ] as ArchiveRun[]);
    render(<ArchiveRuns archiveId={1} />);
    const rows = screen.getAllByTestId("archive-run");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveAttribute("data-latest", "true");
    expect(rows[1]).toHaveAttribute("data-latest", "false");
    expect(screen.getByText("P1S")).toBeInTheDocument();
    expect(screen.getByText("fail")).toBeInTheDocument();
  });

  it("badge renders only for 2+ runs", () => {
    const { rerender } = render(<RunCountBadge count={1} />);
    expect(screen.queryByText(/run/i)).not.toBeInTheDocument();
    rerender(<RunCountBadge count={3} />);
    expect(screen.getByText("3 runs")).toBeInTheDocument();
  });
});
