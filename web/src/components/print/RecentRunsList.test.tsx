import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ArchiveOut } from "@/api/types";
import { RecentRunsList } from "@/components/print/RecentRunsList";
import { totalGrams } from "@/lib/totalGrams";

vi.mock("@/components/print/SourceFiles", () => ({ SourceFiles: () => null }));
vi.mock("@/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/auth")>()),
  useAuth: () => ({ data: { id: 1, username: "tester" }, can: canBox.current }),
}));

const { patchMock, canBox, archiveDataBox } = vi.hoisted(() => ({
  patchMock: vi.fn(),
  canBox: { current: (): boolean => true },
  archiveDataBox: { current: undefined as ArchiveOut | undefined },
}));

vi.mock("@/api/archives", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/archives")>()),
  usePatchRun: () => ({ mutate: patchMock, isPending: false }),
  useArchive: () => ({ data: archiveDataBox.current }),
}));

// `RecentRunsList` renders `RunDetailSheet`, which (P5 Task 11) now also
// renders the shared `PrintDialog` for its Reprint button -- that's a real
// react-query consumer with no `QueryClientProvider` in this file's render
// tree, so it's stubbed out here (unit-tested on its own in
// `PrintDialog.test.tsx`).
vi.mock("@/components/print/ArchiveExtras", () => ({
  VerdictCard: ({ canWrite }: { canWrite: boolean }) => <div data-testid="verdict-card">{String(canWrite)}</div>,
  ArchiveEditor: ({ canWrite, canOverride, isAdmin }: { canWrite: boolean; canOverride: boolean; isAdmin: boolean }) => (
    <div data-testid="archive-editor">{`${canWrite}/${canOverride}/${isAdmin}`}</div>
  ),
  PhotoGallery: () => null,
}));
vi.mock("@/components/print/ArchiveToolpath", () => ({ ArchiveToolpath: () => null }));
vi.mock("@/components/print/TimelapseEditorDialog", () => ({ TimelapseEditorDialog: () => null }));
vi.mock("@/components/print/PrintDialog", () => ({ PrintDialog: () => null }));

function run(overrides: Partial<ArchiveOut> = {}): ArchiveOut {
  return {
    id: 1,
    print_job_id: 10,
    printer_id: 1,
    printer_name: "Bambu A1",
    origin: "app",
    subtask_name: "dragon.gcode.3mf",
    model_id: 7,
    model_slug: "articulated-dragon",
    model_name: "Articulated Dragon",
    revision_id: 2,
    result: "completed",
    duration_s: 3600,
    est_filament: [{ grams: 20 }, { grams: 5 }],
    plate: 1,
    fetch_state: "done",
    fetch_error: null,
    has_thumbnail: false,
    has_file: true,
    has_snapshot: false,
    has_timelapse: false,
    media_note: null,
    started_at: "2026-10-22T09:00:00Z",
    finished_at: "2026-10-22T10:00:00Z",
    created_at: "2026-10-22T10:00:00Z",
    created_by_user_id: 1,
    run: {
      id: 55,
      model_id: 7,
      print_job_id: 10,
      source: "printer",
      quality: null,
      failure_reason: null,
      filament_cost: null,
      machine_cost: null,
      energy_kwh: null,
      energy_cost: null,
      total_cost: 1.25,
      created_by_user_id: 1,
      printed_at: "2026-10-22T10:00:00Z",
      printer_name: "Bambu A1",
      filament: null,
      filament_g: 18,
      result: "success",
      duration_min: 60,
      notes: null,
      created_at: "2026-10-22T10:00:00Z",
      material_id: null,
      material: null,
      model_slug: null,
      model_name: null,
    },
    ...overrides,
  };
}

beforeEach(() => {
  patchMock.mockClear();
  canBox.current = () => true;
  archiveDataBox.current = undefined;
});

describe("totalGrams", () => {
  it("sums per-tray estimates when present", () => {
    expect(totalGrams(run())).toBe(25);
  });

  it("falls back to the graded run's filament_g otherwise", () => {
    expect(totalGrams(run({ est_filament: null }))).toBe(18);
  });
});

describe("RecentRunsList", () => {
  it("shows an empty state with no runs", () => {
    render(<RecentRunsList runs={[]} />);
    expect(screen.getByText("No runs yet.")).toBeInTheDocument();
  });

  it("renders name, printer, grams, and cost", () => {
    render(<RecentRunsList runs={[run()]} />);

    expect(screen.getByText("Articulated Dragon")).toBeInTheDocument();
    expect(screen.getByText("Bambu A1")).toBeInTheDocument();
    expect(screen.getByText("25g")).toBeInTheDocument();
    expect(screen.getByText("1.25")).toBeInTheDocument();
  });

  it("limits the rows shown to `limit`", () => {
    render(<RecentRunsList runs={[run({ id: 1 }), run({ id: 2, model_name: "Second" })]} limit={1} />);

    expect(screen.getByText("Articulated Dragon")).toBeInTheDocument();
    expect(screen.queryByText("Second")).not.toBeInTheDocument();
  });

  it("hides Good/Reject when the caller lacks library:write", () => {
    canBox.current = () => false;
    render(<RecentRunsList runs={[run()]} />);

    expect(screen.queryByRole("button", { name: "Good" })).not.toBeInTheDocument();
  });

  // Fix-review finding 6: a graded-good run's Good button used to render
  // `variant="default"`, the brand amber -- the same color `printing`
  // uses, so a finished/graded run visually read as "in progress". It
  // must be `secondary` with a state-finished outline instead.
  it("gives a graded-good run's Good button a secondary variant with a state-finished outline, not amber default", () => {
    render(<RecentRunsList runs={[run({ run: { ...run().run!, quality: "good" } })]} />);

    const goodButton = screen.getByRole("button", { name: "Good" });
    expect(goodButton).toHaveAttribute("data-variant", "secondary");
    expect(goodButton.className).toContain("state-finished");
  });

  it("clicking Good PATCHes /prints/{id} with quality good", async () => {
    render(<RecentRunsList runs={[run()]} />);

    fireEvent.click(screen.getByRole("button", { name: "Good" }));

    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledExactlyOnceWith({ id: 55, patch: { quality: "good" } }),
    );
  });

  it("clicking a row opens the RunDetailSheet", async () => {
    archiveDataBox.current = run();
    render(<RecentRunsList runs={[run()]} />);

    fireEvent.click(screen.getByText("Articulated Dragon"));

    expect(await screen.findByText("Archived print run")).toBeInTheDocument();
  });
});
