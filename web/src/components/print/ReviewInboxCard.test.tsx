import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ModelSummary, PrinterOut, QueueEntry } from "@/api/types";
import { ReviewInboxCard } from "@/components/print/ReviewInboxCard";

const { inboxDataBox, printersDataBox, reviewMock } = vi.hoisted(() => ({
  inboxDataBox: { current: [] as QueueEntry[] },
  printersDataBox: { current: [] as PrinterOut[] },
  reviewMock: vi.fn(),
}));

vi.mock("@/api/queue", () => ({
  useReviewInbox: () => ({ data: inboxDataBox.current, isLoading: false }),
  useReviewEntry: () => ({ mutate: reviewMock, isPending: false }),
}));

vi.mock("@/api/printers", () => ({
  usePrinters: () => ({ data: printersDataBox.current }),
}));

function model(overrides: Partial<ModelSummary> = {}): ModelSummary {
  return {
    id: 1,
    slug: "dragon",
    name: "Dragon",
    description: null,
    tags: [],
    updated_at: "2026-06-01T12:00:00Z",
    created_at: "2026-06-01T12:00:00Z",
    file_count: 1,
    formats: ["stl"],
    cover: null,
    render_url: null,
    print_time_s: null,
    has_sliced: false,
    source_site: null,
    source_collection_id: null,
    source_collection_title: null,
    favorite: false,
    dims_mm: null,
    best_slicer_file: null,
    printable_file: null,
    ...overrides,
  };
}

function entry(overrides: Partial<QueueEntry> = {}): QueueEntry {
  return {
    id: 1,
    model_id: 1,
    position: 1,
    added_at: "2026-06-01T12:00:00Z",
    model: model(),
    printable_file: null,
    printer_id: null,
    plate: 1,
    copies: 1,
    reserved_cost: 1.5,
    review_state: "pending",
    ...overrides,
  };
}

beforeEach(() => {
  inboxDataBox.current = [];
  printersDataBox.current = [];
  reviewMock.mockClear();
});

describe("ReviewInboxCard", () => {
  it("shows an empty state when nothing is pending", () => {
    render(<ReviewInboxCard />);
    expect(screen.getByText("Nothing pending.")).toBeInTheDocument();
  });

  it("lists pending items with model, plate, printer-or-Any and est cost", () => {
    inboxDataBox.current = [entry({ printer_id: null })];
    render(<ReviewInboxCard />);

    expect(screen.getByText("Dragon")).toBeInTheDocument();
    expect(screen.getByText("Plate 1")).toBeInTheDocument();
    expect(screen.getByText("Any")).toBeInTheDocument();
    expect(screen.getByText("1.50")).toBeInTheDocument();
  });

  it("resolves a named printer when printer_id is set", () => {
    printersDataBox.current = [
      { id: 7, name: "Garage X1", kind: "bambu_lan", host: "h", serial: "s", model: null, enabled: true, options: {}, access_code_set: true, build_volume_mm: null },
    ];
    inboxDataBox.current = [entry({ printer_id: 7 })];
    render(<ReviewInboxCard />);
    expect(screen.getByText("Garage X1")).toBeInTheDocument();
  });

  it("shows who queued the item", () => {
    inboxDataBox.current = [entry({ created_by: { id: 9, username: "jamie" } })];
    render(<ReviewInboxCard />);
    expect(screen.getByText("jamie")).toBeInTheDocument();
  });

  it("approve calls the review endpoint and reject posts a note", async () => {
    inboxDataBox.current = [entry({ id: 3 })];
    render(<ReviewInboxCard />);

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(reviewMock).toHaveBeenCalledExactlyOnceWith({ entryId: 3, body: { action: "approve" } }),
    );
  });
});
