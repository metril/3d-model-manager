import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ArchiveOut, PrintEntry } from "@/api/types";
import { RunDetailSheet } from "@/components/print/RunDetailSheet";

const { archiveMock, patchMock, authMock } = vi.hoisted(() => ({
  archiveMock: vi.fn(),
  patchMock: vi.fn(),
  authMock: vi.fn(),
}));
vi.mock("@/api/archives", () => ({
  useArchive: () => ({ data: archiveMock() }),
  usePatchRun: () => ({ mutate: patchMock, isPending: false }),
  archiveThumbnailUrl: (id: number) => `/t/${id}`,
  archiveFileUrl: (id: number) => `/f/${id}`,
  archiveSnapshotUrl: (id: number) => `/s/${id}`,
  archiveTimelapseUrl: (id: number) => `/v/${id}`,
}));
vi.mock("@/api/auth", () => ({ useAuth: () => authMock() }));
// `PrintDialog` is unit-tested on its own (`PrintDialog.test.tsx`) -- here
// it's stubbed to just a visible marker when open, so this file only
// asserts RunDetailSheet's OWN logic: whether Reprint shows, and that
// clicking it opens the dialog with the archive source.
vi.mock("@/components/print/PrintDialog", () => ({
  PrintDialog: ({ open, source }: { open: boolean; source: { kind: string; archiveId?: number } }) =>
    open ? <div data-testid="print-dialog">{`${source.kind}:${source.archiveId}`}</div> : null,
}));

const run = {
  id: 7,
  quality: null,
  failure_reason: null,
  filament_g: 12.5,
  filament_cost: 5,
  machine_cost: 3,
  energy_cost: null,
  total_cost: 8,
  created_by_user_id: 1,
} as unknown as PrintEntry;
const archive = {
  id: 3,
  model_name: "Benchy",
  subtask_name: "benchy.3mf",
  printer_name: "A1",
  result: "completed",
  duration_s: 3600,
  est_filament: [{ slot: 1, type: "PLA", color: "#ff0000", grams: 12.5 }],
  fetch_state: "done",
  has_thumbnail: true,
  has_file: true,
  created_by_user_id: 1,
  run,
} as unknown as ArchiveOut;

function auth(over: { id?: number; role?: string; writer?: boolean; canPrint?: boolean }) {
  authMock.mockReturnValue({
    data: { id: over.id ?? 1, role: over.role ?? "member" },
    can: (p: string) =>
      p === "library:write"
        ? (over.writer ?? true)
        : p === "printers:print"
          ? (over.canPrint ?? false)
          : p === "admin" && over.role === "admin",
  });
}
function renderSheet() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <RunDetailSheet archiveId={3} onOpenChange={() => {}} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  patchMock.mockReset();
  archiveMock.mockReturnValue(archive);
  auth({});
});

describe("RunDetailSheet", () => {
  it("shows cost rows", () => {
    renderSheet();
    expect(screen.getByText("Filament").nextSibling).toHaveTextContent("5.00");
    expect(screen.getByText("Machine").nextSibling).toHaveTextContent("3.00");
    expect(screen.getByText("Energy").nextSibling).toHaveTextContent("--");
    expect(screen.getByText("Total").nextSibling).toHaveTextContent("8.00");
    expect(screen.queryByText(/reprint/i)).not.toBeInTheDocument();
  });
  it("shows the Energy used row only when energy_kwh is set", () => {
    archiveMock.mockReturnValue({ ...archive, run: { ...run, energy_kwh: 1.25 } });
    renderSheet();
    expect(screen.getByText("Energy used").nextSibling).toHaveTextContent("1.25 kWh");
  });
  it("hides the Energy used row when energy_kwh is null", () => {
    renderSheet();
    expect(screen.queryByText("Energy used")).not.toBeInTheDocument();
  });
  it("Good patches quality", () => {
    renderSheet();
    fireEvent.click(screen.getByRole("button", { name: "Good" }));
    expect(patchMock).toHaveBeenCalledWith({ id: 7, patch: { quality: "good" } }, expect.anything());
  });
  it("Reject asks for a failure reason", () => {
    renderSheet();
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    fireEvent.change(screen.getByLabelText("Failure reason"), { target: { value: "spaghetti" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(patchMock).toHaveBeenCalledWith(
      { id: 7, patch: { quality: "reject", failure_reason: "spaghetti" } },
      expect.anything(),
    );
  });
  it("saves actual grams", () => {
    renderSheet();
    fireEvent.change(screen.getByLabelText("Actual grams"), { target: { value: "10.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save grams" }));
    expect(patchMock).toHaveBeenCalledWith({ id: 7, patch: { filament_g: 10.5 } }, expect.anything());
  });
  it("hides grading for viewers and non-owner members", () => {
    auth({ writer: false });
    const { unmount } = renderSheet();
    expect(screen.queryByRole("button", { name: "Good" })).not.toBeInTheDocument();
    unmount();
    auth({ id: 2 });
    renderSheet();
    expect(screen.queryByRole("button", { name: "Good" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Actual grams")).not.toBeInTheDocument();
  });
  it("lets admins grade others' runs", () => {
    auth({ id: 2, role: "admin" });
    renderSheet();
    expect(screen.getByRole("button", { name: "Good" })).toBeInTheDocument();
  });
  it("shows the captured snapshot when has_snapshot", () => {
    archiveMock.mockReturnValue({ ...archive, has_snapshot: true });
    renderSheet();
    expect(screen.getByAltText("Captured snapshot")).toHaveAttribute("src", "/s/3");
  });
  it("hides the snapshot img when has_snapshot is false", () => {
    archiveMock.mockReturnValue({ ...archive, has_snapshot: false });
    renderSheet();
    expect(screen.queryByAltText("Captured snapshot")).not.toBeInTheDocument();
  });
  it("shows the timelapse player (poster = snapshot) when has_timelapse", () => {
    archiveMock.mockReturnValue({ ...archive, has_snapshot: true, has_timelapse: true });
    renderSheet();
    const video = document.querySelector("video");
    expect(video).toHaveAttribute("src", "/v/3");
    expect(video).toHaveAttribute("poster", "/s/3");
  });
  it("hides the timelapse player when has_timelapse is false", () => {
    archiveMock.mockReturnValue({ ...archive, has_timelapse: false });
    renderSheet();
    expect(document.querySelector("video")).not.toBeInTheDocument();
  });
  it("shows media_note as a muted line", () => {
    archiveMock.mockReturnValue({ ...archive, media_note: "Timelapse unavailable: ffmpeg missing" });
    renderSheet();
    expect(screen.getByText("Timelapse unavailable: ffmpeg missing")).toBeInTheDocument();
  });

  it("hides Reprint without printers:print", () => {
    renderSheet();
    expect(screen.queryByRole("button", { name: "Reprint" })).not.toBeInTheDocument();
  });

  it("shows Reprint and opens the PrintDialog with the archive source when allowed", () => {
    auth({ canPrint: true });
    renderSheet();
    fireEvent.click(screen.getByRole("button", { name: "Reprint" }));
    expect(screen.getByTestId("print-dialog")).toHaveTextContent("archive:3");
  });
});
