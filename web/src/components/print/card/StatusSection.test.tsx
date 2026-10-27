import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ActiveJobOut, ArchiveOut } from "@/api/types";

import { etaClock } from "./etaClock";
import { StatusSection } from "./StatusSection";
import { a1Mini, renderCard } from "./testUtils";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock } };
});

const JOB = { id: 5, origin: "app", subtask_name: "benchy", model_name: "Benchy", plate: 1 } as unknown as ActiveJobOut;
const RUN = { id: 9, print_job_id: 4, subtask_name: "cube", model_name: "Cube", result: "completed", has_thumbnail: true } as unknown as ArchiveOut;
const PRINTING = { gcode_state: "RUNNING", mc_percent: 42, layer_num: 10, total_layer_num: 100, mc_remaining_time: 75, stage: "Printing" };

describe("StatusSection", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    getMock.mockResolvedValue({ job_id: 5, plate: 1, objects: [] });
  });

  it("printing: name, big percent, progress, ETA row, layer, started-by; generic stage hidden", async () => {
    const it = a1Mini({}, PRINTING);
    renderCard(<StatusSection printerId={1} status={it.status} activeJob={JOB} lastRuns={[]} canControl />);
    expect(await screen.findByText("Benchy")).toBeInTheDocument();
    expect(screen.queryByText("Printing")).toBeNull();
    expect(screen.getByTestId("job-percent")).toHaveTextContent("42%");
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "42");
    expect(screen.getByText("1h 15m left")).toBeInTheDocument();
    expect(screen.getByText("Layer 10/100")).toBeInTheDocument();
    expect(screen.getByText(/ETA/)).toBeInTheDocument();
    expect(screen.getByText(/from the app/)).toBeInTheDocument();
  });

  it("a specific stage is shown; paused uses a striped fill", async () => {
    const it = a1Mini({}, { ...PRINTING, gcode_state: "PAUSE", stage: "Filament runout" });
    renderCard(<StatusSection printerId={1} status={it.status} activeJob={JOB} lastRuns={[]} canControl />);
    expect(await screen.findByText("Filament runout")).toBeInTheDocument();
    const fill = screen.getByRole("progressbar").firstElementChild as HTMLElement;
    expect(fill.style.backgroundImage).toContain("repeating-linear-gradient");
  });

  it("printing: thumbnail comes from the active job's thumbnail_url", async () => {
    const it = a1Mini({}, PRINTING);
    const job = { ...JOB, thumbnail_url: "/api/blobs/abc/plates/1/thumb" } as ActiveJobOut;
    const { container } = renderCard(
      <StatusSection printerId={1} status={it.status} activeJob={job} lastRuns={[RUN]} canControl />,
    );
    await screen.findByText("Benchy");
    expect(container.querySelector("img")).toHaveAttribute("src", "/api/blobs/abc/plates/1/thumb");
  });

  it("printing without a thumbnail_url shows the placeholder", async () => {
    const it = a1Mini({}, PRINTING);
    const { container } = renderCard(<StatusSection printerId={1} status={it.status} activeJob={JOB} lastRuns={[]} canControl />);
    await screen.findByText("Benchy");
    expect(container.querySelector("img")).toBeNull();
  });

  it("idle: Last: ... with archive thumbnail, plate-clear pill when awaiting", async () => {
    const it = a1Mini({}, { gcode_state: "FINISH", awaiting_plate_clear: true });
    const { container } = renderCard(
      <StatusSection printerId={1} status={it.status} activeJob={null} lastRuns={[RUN]} canControl />,
    );
    expect(await screen.findByText(/Last: cube · completed/)).toBeInTheDocument();
    expect(screen.getByText("Plate needs clearing")).toBeInTheDocument();
    expect(container.querySelector("img")).toHaveAttribute("src", "/api/archives/9/thumbnail");
  });

  it("offline: only the last print, no repeated status", async () => {
    const it = a1Mini({}, { online: false });
    renderCard(<StatusSection printerId={1} status={it.status} activeJob={null} lastRuns={[RUN]} canControl />);
    expect(await screen.findByText("Last print: cube")).toBeInTheDocument();
    expect(screen.queryByText(/Offline/)).toBeNull();
  });

  it("offline: shows last seen when the report time is known; progress bars are named", async () => {
    const seen = new Date(Date.now() - 3 * 3600_000).toISOString();
    const off = a1Mini({}, { online: false, last_report_at: seen });
    const { unmount } = renderCard(<StatusSection printerId={1} status={off.status} activeJob={null} lastRuns={[]} canControl />);
    expect(await screen.findByText(/Offline · last seen/)).toBeInTheDocument();
    unmount();
    const on = a1Mini({}, PRINTING);
    renderCard(<StatusSection printerId={1} status={on.status} activeJob={JOB} lastRuns={[]} canControl />);
    expect(await screen.findByRole("progressbar", { name: "Print progress" })).toBeInTheDocument();
  });

  it("etaClock formats null / valid", () => {
    expect(etaClock(null)).toBeNull();
    expect(etaClock(30, new Date("2026-01-01T10:00:00"))).toMatch(/10:30/);
  });
});
