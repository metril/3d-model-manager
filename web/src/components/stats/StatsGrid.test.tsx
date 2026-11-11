import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { StatsDashboard } from "@/api/stats";
import { reloadLayoutLock, setLayoutLocked } from "@/lib/layoutLock";
import { StatsGrid } from "./StatsGrid";

vi.mock("@/components/stats/registry", () => ({ WIDGETS: { kpis: () => <div />, trend: () => <div /> } }));

const widgets = [
  { id: "kpis", size: 1 },
  { id: "trend", size: 2 },
] as never;

beforeEach(() => {
  localStorage.clear();
  reloadLayoutLock();
});

describe("StatsGrid lock", () => {
  it("hides drag handles and resize buttons when locked", () => {
    render(<StatsGrid widgets={widgets} data={{} as StatsDashboard} onReorder={() => {}} onResize={() => {}} />);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("shows them when unlocked", () => {
    setLayoutLocked(false);
    render(<StatsGrid widgets={widgets} data={{} as StatsDashboard} onReorder={() => {}} onResize={() => {}} />);
    expect(screen.getAllByRole("button")).toHaveLength(4);
  });
});
