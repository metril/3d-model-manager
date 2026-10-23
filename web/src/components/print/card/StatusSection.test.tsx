import { fireEvent, screen, waitFor } from "@testing-library/react";
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

  it("printing: name, stage, progress, remaining, layer, started-by", async () => {
    const it = a1Mini({}, PRINTING);
    renderCard(<StatusSection printerId={1} status={it.status} activeJob={JOB} lastRuns={[]} canControl />);
    expect(await screen.findByText("Benchy")).toBeInTheDocument();
    expect(screen.getByText("Printing")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "42");
    expect(screen.getByText("1h 15m left")).toBeInTheDocument();
    expect(screen.getByText("Layer 10/100")).toBeInTheDocument();
    expect(screen.getByText(/ETA/)).toBeInTheDocument();
    expect(screen.getByText(/from the app/)).toBeInTheDocument();
  });

  it("idle: Last: ... with archive thumbnail, plate-clear pill after FINISH", async () => {
    const it = a1Mini({}, { gcode_state: "FINISH" });
    const { container } = renderCard(
      <StatusSection printerId={1} status={it.status} activeJob={null} lastRuns={[RUN]} canControl />,
    );
    expect(await screen.findByText(/Last: cube · completed/)).toBeInTheDocument();
    expect(screen.getByText("Clear plate")).toBeInTheDocument();
    expect(container.querySelector("img")).toHaveAttribute("src", "/api/archives/9/thumbnail");
  });

  it("skip-objects button with badge opens the dialog and posts the selection", async () => {
    getMock.mockResolvedValue({
      job_id: 5,
      plate: 1,
      objects: [
        { id: 10, name: "Part A", plate: 1 },
        { id: 11, name: "Part B", plate: 1 },
        { id: 12, name: "Part C", plate: 1 },
      ],
    });
    postMock.mockResolvedValue({ ok: true });
    const it = a1Mini({}, PRINTING);
    renderCard(<StatusSection printerId={1} status={it.status} activeJob={JOB} lastRuns={[]} canControl />);
    const btn = await screen.findByRole("button", { name: "Skip objects" });
    expect(btn).toHaveTextContent("3");
    fireEvent.click(btn);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Part B" }));
    fireEvent.click(screen.getByRole("button", { name: /Skip 1 selected/ }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/skip-objects", { object_ids: [11] }));
  });

  it("no skip button when the viewer cannot control or only one object exists", async () => {
    getMock.mockResolvedValue({ job_id: 5, plate: 1, objects: [{ id: 1, name: "Solo", plate: 1 }] });
    const it = a1Mini({}, PRINTING);
    renderCard(<StatusSection printerId={1} status={it.status} activeJob={JOB} lastRuns={[]} canControl />);
    await screen.findByText("Benchy");
    await waitFor(() => expect(getMock).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "Skip objects" })).toBeNull();
  });

  it("offline text", async () => {
    const it = a1Mini({}, { online: false });
    renderCard(<StatusSection printerId={1} status={it.status} activeJob={null} lastRuns={[]} canControl />);
    expect(await screen.findByText(/Offline/)).toBeInTheDocument();
  });

  it("etaClock formats null / valid", () => {
    expect(etaClock(null)).toBeNull();
    expect(etaClock(30, new Date("2026-01-01T10:00:00"))).toMatch(/10:30/);
  });
});
