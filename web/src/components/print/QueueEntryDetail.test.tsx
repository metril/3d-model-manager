import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { QueueEntry } from "@/api/types";
import { QueueEntryDetail } from "@/components/print/QueueEntryDetail";

const { cancelMock } = vi.hoisted(() => ({ cancelMock: vi.fn() }));
vi.mock("@/api/queue", () => ({
  useCancelQueueSlice: () => ({ mutate: cancelMock, isPending: false }),
}));

const base = { id: 7, model_id: 1, position: 1, added_at: "2026-01-01T00:00:00Z", model: {} } as unknown as QueueEntry;

beforeEach(() => cancelMock.mockReset());

describe("QueueEntryDetail", () => {
  it("shows time, grams and plates of the sliced file", () => {
    render(
      <QueueEntryDetail
        entry={{
          ...base,
          state: "ready",
          sliced_file_id: 9,
          sliced_meta: {
            file_name: "cube - r (q7 p1).gcode.3mf",
            print_time_s: 5400,
            filament_g: 12.4,
            plate_count: 2,
            plates: [
              { index: 1, prediction_s: 3000, weight_g: 7 },
              { index: 2, prediction_s: 2400, weight_g: 5 },
            ],
          },
        }}
      />,
    );
    expect(screen.getByText(/1h 30m · 12 g · 2 plates/)).toBeInTheDocument();
    expect(screen.getByText(/Plate 2 · 40m · 5 g/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "View log" })).not.toBeInTheDocument();
  });

  it("opens the log dialog when a failed slice left output", () => {
    render(<QueueEntryDetail entry={{ ...base, state: "failed", attempts: 3, last_error: "exited 3", last_log: "boom on stderr" }} />);
    expect(screen.getByText(/exited 3 \(3\/3 attempts\)/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "View log" }));
    expect(screen.getByLabelText("Log output")).toHaveTextContent("boom on stderr");
  });

  it("cancels an in-flight slice", () => {
    render(<QueueEntryDetail entry={{ ...base, state: "slicing" }} />);
    expect(screen.getByText("Not sliced yet.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel slicing" }));
    expect(cancelMock).toHaveBeenCalledWith(7);
  });

  it("offers no cancel outside slicing", () => {
    render(<QueueEntryDetail entry={{ ...base, state: "waiting" }} />);
    expect(screen.queryByRole("button", { name: "Cancel slicing" })).not.toBeInTheDocument();
  });
});
