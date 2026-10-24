import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ModelSummary, PrinterOut, QueueEntry, QueueState } from "@/api/types";
import { QueuePage } from "@/pages/QueuePage";

// Mock the queue hooks directly (same pattern the old QueuePage.test.tsx
// used) -- QueuePage's own logic (lane grouping, the chip, retry gating,
// the include-done toggle) is what's under test, not the underlying fetch
// plumbing (already covered by `api/queue.ts`'s own hook conventions).
const {
  queueDataBox, moveMock, removeMock, retryMock, reviewMock, printersDataBox, includeDoneCalls, authCanBox,
  startMock, cancelMock, resumeMock, patchMock,
} =
  vi.hoisted(() => ({
    startMock: vi.fn(),
    cancelMock: vi.fn(),
    resumeMock: vi.fn(),
    patchMock: vi.fn(),
    queueDataBox: { current: [] as QueueEntry[] },
    moveMock: vi.fn(),
    removeMock: vi.fn(),
    retryMock: vi.fn(),
    reviewMock: vi.fn(),
    printersDataBox: { current: [] as PrinterOut[] },
    includeDoneCalls: [] as boolean[],
    authCanBox: { current: (_p: string) => true as boolean },
  }));

vi.mock("@/api/queue", () => ({
  useQueue: ({ includeDone }: { includeDone?: boolean } = {}) => {
    includeDoneCalls.push(!!includeDone);
    return { data: queueDataBox.current, isLoading: false, isError: false };
  },
  useMoveQueueEntry: () => ({ mutate: moveMock, isPending: false }),
  useRemoveQueueEntry: () => ({ mutate: removeMock, isPending: false }),
  useRetryQueueEntry: () => ({ mutate: retryMock, isPending: false }),
  useReviewEntry: () => ({ mutate: reviewMock, isPending: false }),
  useStartQueueEntry: () => ({ mutate: startMock, isPending: false }),
  useCancelSchedule: () => ({ mutate: cancelMock, isPending: false }),
  useResumePrinterQueue: () => ({ mutate: resumeMock, isPending: false }),
  usePatchQueueEntry: () => ({ mutate: patchMock, isPending: false }),
  useBatches: () => ({ data: [], isLoading: false }),
}));

vi.mock("@/api/printers", () => ({
  usePrinters: () => ({ data: printersDataBox.current }),
}));

vi.mock("@/api/auth", () => ({
  useAuth: () => ({ can: (p: string) => authCanBox.current(p) }),
}));

function model(overrides: Partial<ModelSummary> = {}): ModelSummary {
  return {
    id: 1,
    slug: "articulated-dragon",
    name: "Articulated Dragon",
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

const PRINTER: PrinterOut = {
  id: 1,
  name: "Bambu A1",
  kind: "bambu_lan",
  host: "192.168.1.50",
  serial: "AC12345",
  model: "A1 mini",
  enabled: true,
  options: {},
  access_code_set: true,
  build_volume_mm: null,
};

function entry(overrides: Partial<QueueEntry> = {}): QueueEntry {
  return {
    id: 1,
    model_id: 1,
    position: 1,
    added_at: "2026-06-01T12:00:00Z",
    model: model(),
    printable_file: null,
    printer_id: null,
    assigned_printer_id: null,
    recipe_name: null,
    plate: 1,
    copies: 1,
    state: "waiting" as QueueState,
    attempts: 0,
    last_error: null,
    file_name: null,
    ...overrides,
  };
}

function renderQueuePage() {
  const rootRoute = createRootRoute();
  const queueRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: QueuePage });
  const detailRoute = createRoute({ getParentRoute: () => rootRoute, path: "/models/$slug", component: () => null });
  const router = createRouter({
    routeTree: rootRoute.addChildren([queueRoute, detailRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  queueDataBox.current = [];
  moveMock.mockClear();
  removeMock.mockClear();
  retryMock.mockClear();
  reviewMock.mockClear();
  startMock.mockClear();
  cancelMock.mockClear();
  resumeMock.mockClear();
  patchMock.mockClear();
  printersDataBox.current = [PRINTER];
  includeDoneCalls.length = 0;
  authCanBox.current = () => true;
});

describe("QueuePage review/budget chips (P7 Task 9)", () => {
  it("renders a Pending review chip and ReviewActions for a reviewer", async () => {
    queueDataBox.current = [entry({ review_state: "pending" })];
    renderQueuePage();

    expect(await screen.findByText("Pending review")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
  });

  it("hides ReviewActions for a non-reviewer even on a pending entry", async () => {
    authCanBox.current = (p: string) => p !== "queue:review";
    queueDataBox.current = [entry({ review_state: "pending" })];
    renderQueuePage();

    expect(await screen.findByText("Pending review")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("renders a Rejected chip", async () => {
    queueDataBox.current = [entry({ review_state: "rejected" })];
    renderQueuePage();
    expect(await screen.findByText("Rejected")).toBeInTheDocument();
  });

  it("renders an Over budget chip with a Retry budget button", async () => {
    queueDataBox.current = [entry({ hold_reason: "over_budget", state: "held" })];
    renderQueuePage();

    expect(await screen.findByText("Over budget")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry budget" }));
    await waitFor(() => expect(retryMock).toHaveBeenCalledExactlyOnceWith(1));
  });

  it("renders a Held chip with the reason when printer_hold_reason is set", async () => {
    queueDataBox.current = [entry({ printer_hold_reason: "Smoke detected" })];
    renderQueuePage();

    const chip = await screen.findByText("Held");
    expect(chip.closest("[title]")).toHaveAttribute("title", "Smoke detected");
  });

  it("renders no Held chip when printer_hold_reason is null", async () => {
    queueDataBox.current = [entry({ printer_hold_reason: null })];
    renderQueuePage();

    await screen.findByText("Articulated Dragon");
    expect(screen.queryByText("Held")).not.toBeInTheDocument();
  });

  it("shows both the Held chip and ReviewChips when both apply", async () => {
    queueDataBox.current = [entry({ printer_hold_reason: "All printers held", review_state: "pending" })];
    renderQueuePage();

    expect(await screen.findByText("Held")).toBeInTheDocument();
    expect(screen.getByText("Pending review")).toBeInTheDocument();
  });

  it("approving a pending entry calls the review mutation", async () => {
    queueDataBox.current = [entry({ id: 3, review_state: "pending" })];
    renderQueuePage();

    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(reviewMock).toHaveBeenCalledExactlyOnceWith({ entryId: 3, body: { action: "approve" } }),
    );
  });
});

describe("QueuePage lanes", () => {
  it("renders one lane per visible printer plus Any printer", async () => {
    renderQueuePage();
    expect(await screen.findByText("Bambu A1")).toBeInTheDocument();
    expect(screen.getByText("Any printer")).toBeInTheDocument();
  });

  it("groups an entry with a printer_id into that printer's lane", async () => {
    queueDataBox.current = [entry({ printer_id: 1 })];
    renderQueuePage();

    const bambuLane = (await screen.findByText("Bambu A1")).closest('[data-slot="card"]');
    expect(bambuLane).toHaveTextContent("Articulated Dragon");
    const anyLane = screen.getByText("Any printer").closest('[data-slot="card"]');
    expect(anyLane).toHaveTextContent("Nothing here.");
  });

  it("groups a dispatched entry (assigned_printer_id, no printer_id) into that printer's lane", async () => {
    queueDataBox.current = [entry({ printer_id: null, assigned_printer_id: 1, state: "dispatched" })];
    renderQueuePage();

    const bambuLane = (await screen.findByText("Bambu A1")).closest('[data-slot="card"]');
    expect(bambuLane).toHaveTextContent("Articulated Dragon");
  });

  it("groups an entry with neither printer_id nor assigned_printer_id as Any printer", async () => {
    queueDataBox.current = [entry({ printer_id: null, assigned_printer_id: null })];
    renderQueuePage();

    const anyLane = (await screen.findByText("Any printer")).closest('[data-slot="card"]');
    expect(anyLane).toHaveTextContent("Articulated Dragon");
  });

  it("shows recipe name or 'As sliced' when there's none", async () => {
    queueDataBox.current = [
      entry({ id: 1, recipe_name: "Standard PLA" }),
      entry({ id: 2, recipe_name: null, model: model({ slug: "other", name: "Other Model" }) }),
    ];
    renderQueuePage();

    expect(await screen.findByText("Standard PLA")).toBeInTheDocument();
    expect(screen.getByText("As sliced")).toBeInTheDocument();
  });
});

describe("QueuePage state chip", () => {
  it.each<QueueState>(["waiting", "slicing", "ready", "dispatched", "held", "failed", "done"])(
    "renders a chip for state %s",
    async (state) => {
      queueDataBox.current = [entry({ state })];
      renderQueuePage();
      const label = state.charAt(0).toUpperCase() + state.slice(1);
      expect(await screen.findByText(label)).toBeInTheDocument();
    },
  );

  it("shows last_error and attempts in the chip's title", async () => {
    queueDataBox.current = [entry({ state: "failed", last_error: "slicer timed out", attempts: 2 })];
    renderQueuePage();

    const chip = await screen.findByText("Failed");
    expect(chip).toHaveAttribute("title", expect.stringContaining("2/3 attempts"));
    expect(chip).toHaveAttribute("title", expect.stringContaining("slicer timed out"));
  });
});

describe("QueuePage retry/remove", () => {
  it("shows Retry only for failed or held entries", async () => {
    queueDataBox.current = [
      entry({ id: 1, state: "failed" }),
      entry({ id: 2, state: "held", model: model({ slug: "m2", name: "M2" }) }),
      entry({ id: 3, state: "waiting", model: model({ slug: "m3", name: "M3" }) }),
      entry({ id: 4, state: "ready", model: model({ slug: "m4", name: "M4" }) }),
    ];
    renderQueuePage();
    await screen.findByText("M3");

    expect(screen.getByRole("button", { name: "Retry Articulated Dragon" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry M2" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry M3" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry M4" })).not.toBeInTheDocument();
  });

  it("retrying calls the retry mutation with the entry id", async () => {
    queueDataBox.current = [entry({ id: 5, state: "failed" })];
    renderQueuePage();

    fireEvent.click(await screen.findByRole("button", { name: "Retry Articulated Dragon" }));
    await waitFor(() => expect(retryMock).toHaveBeenCalledExactlyOnceWith(5));
  });

  it("removing an entry calls the delete mutation with its id", async () => {
    queueDataBox.current = [entry({ id: 7 })];
    renderQueuePage();

    fireEvent.click(await screen.findByRole("button", { name: "Remove Articulated Dragon" }));
    await waitFor(() => expect(removeMock).toHaveBeenCalledExactlyOnceWith(7));
  });
});

describe("QueuePage include-done toggle", () => {
  it("fetches without includeDone by default, and with it once toggled on", async () => {
    renderQueuePage();
    await screen.findByText("Any printer");
    expect(includeDoneCalls.at(-1)).toBe(false);

    fireEvent.click(screen.getByRole("switch", { name: "Show finished" }));
    await waitFor(() => expect(includeDoneCalls.at(-1)).toBe(true));
  });
});

describe("QueuePage scheduling chips (T4)", () => {
  it("shows a scheduled chip with a working cancel-schedule button", async () => {
    queueDataBox.current = [entry({ id: 7, scheduled_at: "2026-10-25T14:30:00Z" })];
    renderQueuePage();
    expect(await screen.findByText(/^Scheduled /)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel schedule" }));
    expect(cancelMock).toHaveBeenCalledWith(7);
  });

  it("manual-start entries get a Start button only for queue:review", async () => {
    queueDataBox.current = [entry({ id: 8, manual_start: true, released_at: null })];
    renderQueuePage();
    expect(await screen.findByText("Manual start")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    expect(startMock).toHaveBeenCalledWith(8);
  });

  it("hides Start without queue:review", async () => {
    authCanBox.current = () => false;
    queueDataBox.current = [entry({ id: 8, manual_start: true })];
    renderQueuePage();
    expect(await screen.findByText("Manual start")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start" })).not.toBeInTheDocument();
  });

  it("a skipped entry shows its reason, a row Resume and a lane Resume that call resume with the printer id", async () => {
    queueDataBox.current = [
      entry({ id: 9, state: "skipped", printer_id: 1, skipped_reason: "previous_failed", hold_reason: "previous_failed" }),
    ];
    renderQueuePage();
    expect(await screen.findByText("Skipped: previous print failed")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Resume printer" }));
    expect(resumeMock).toHaveBeenCalledWith(1);
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    expect(resumeMock).toHaveBeenCalledTimes(2);
  });

  it("renders stage, power-off and batch chips", async () => {
    queueDataBox.current = [
      entry({ id: 10, state: "ready", stage: "soaking", auto_off_after: true, batch: { id: 4, name: "Gears" }, batch_id: 4 }),
    ];
    renderQueuePage();
    expect(await screen.findByText("Soaking")).toBeInTheDocument();
    expect(screen.getByText("Power off after")).toBeInTheDocument();
    expect(screen.getByText("Batch: Gears")).toBeInTheDocument();
  });

  it("the inline editor patches the scheduling fields (cleared fields are explicit nulls/false)", async () => {
    queueDataBox.current = [entry({ id: 11, manual_start: false })];
    renderQueuePage();
    fireEvent.click(await screen.findByRole("button", { name: "Schedule" }));
    fireEvent.click(await screen.findByRole("switch", { name: "Manual start" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(patchMock).toHaveBeenCalledWith(
      {
        entryId: 11,
        body: {
          scheduled_at: null,
          manual_start: true,
          require_previous_success: false,
          auto_off_after: false,
          preheat_override: null,
          preheat_chamber_target: null,
        },
      },
      expect.anything(),
    );
  });
});
