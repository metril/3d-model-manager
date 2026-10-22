import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { QueuePage } from "@/pages/QueuePage";
import type { FileOut, PrinterOut, QueueEntry } from "@/api/types";

vi.mock("@/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/auth")>()),
  useAuth: () => ({ data: { id: 1, username: "tester" }, can: () => true, canPrinter: () => true }),
}));


// Mock the queue hooks directly (same pattern as AppShell.test.tsx mocking
// `@/api/auth`/`@/api/features`) -- QueuePage's own logic (ordering, the
// up/down position math, empty state) is what's under test, not the
// underlying fetch plumbing (already covered by the api-hook conventions
// shared with `library.ts`/`jobs.ts`).
const {
  queueDataBox,
  moveMock,
  removeMock,
  featuresDataBox,
  printersDataBox,
  startPrintMock,
  toastSuccessMock,
} = vi.hoisted(() => ({
  queueDataBox: { current: [] as QueueEntry[] },
  moveMock: vi.fn(),
  removeMock: vi.fn(),
  featuresDataBox: { current: { printer_enabled: true } },
  printersDataBox: { current: [] as PrinterOut[] },
  startPrintMock: vi.fn(),
  toastSuccessMock: vi.fn(),
}));

vi.mock("@/api/queue", () => ({
  useQueue: () => ({ data: queueDataBox.current, isLoading: false, isError: false }),
  useMoveQueueEntry: () => ({ mutate: moveMock, isPending: false }),
  useRemoveQueueEntry: () => ({ mutate: removeMock, isPending: false }),
}));

vi.mock("@/api/features", () => ({
  useFeatures: () => ({ data: featuresDataBox.current }),
}));

// `usePrinters`/`useStartPrint` (Round 8 Task 3): QueueRow's own print-button
// gate uses `usePrinters`, and the extracted `SendToPrinterDialog` (rendered
// by `QueuePage.tsx`, unit-tested on its own in
// `SendToPrinterButton.test.tsx`) uses `useStartPrint` for the submit
// mutation -- both mocked at the hook level here too, same posture as
// `@/api/queue` above.
vi.mock("@/api/printers", () => ({
  usePrinters: () => ({ data: printersDataBox.current }),
  useStartPrint: () => ({ mutate: startPrintMock, isPending: false, isError: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: toastSuccessMock, error: vi.fn() },
}));

const PRINTABLE_FILE: FileOut = {
  id: 42,
  revision_id: 5,
  rel_path: "print.gcode.3mf",
  storage_path: "/data/model/rev/print.gcode.3mf",
  blob_hash: "slicedhash",
  size: 4096,
  format: "gcode_3mf",
  kind: "sliced",
  mtime: "2026-06-01T12:00:00Z",
  verified_at: "2026-06-01T12:00:05Z",
  meta: null,
  thumb_ready: false,
  glb_status: null,
  glb_preview_ready: false,
};

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
    model: {
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
    },
    printable_file: null,
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
  featuresDataBox.current = { printer_enabled: true };
  printersDataBox.current = [PRINTER];
  startPrintMock.mockClear();
  toastSuccessMock.mockClear();
});

describe("QueuePage", () => {
  it("shows an inviting empty state when the queue is empty", async () => {
    renderQueuePage();

    expect(await screen.findByText(/Your print queue is empty/)).toBeInTheDocument();
  });

  it("renders entries in order with their position", async () => {
    queueDataBox.current = [
      entry({ id: 1, position: 1, model: { ...entry().model, name: "First Model" } }),
      entry({ id: 2, position: 2, model: { ...entry().model, slug: "second-model", name: "Second Model" } }),
    ];

    renderQueuePage();

    expect(await screen.findByText("First Model")).toBeInTheDocument();
    expect(screen.getByText("Second Model")).toBeInTheDocument();
    const positions = screen.getAllByText(/^[12]$/).map((node) => node.textContent);
    expect(positions).toEqual(["1", "2"]);
  });

  it("moving an entry up calls move with position - 1", async () => {
    queueDataBox.current = [
      entry({ id: 1, position: 1 }),
      entry({ id: 2, position: 2, model: { ...entry().model, slug: "second-model", name: "Second Model" } }),
    ];

    renderQueuePage();
    await screen.findByText("Second Model");

    fireEvent.click(screen.getByRole("button", { name: "Move Second Model up" }));

    await waitFor(() => expect(moveMock).toHaveBeenCalledExactlyOnceWith({ entryId: 2, position: 1 }));
  });

  it("moving an entry down calls move with position + 1", async () => {
    queueDataBox.current = [
      entry({ id: 1, position: 1 }),
      entry({ id: 2, position: 2, model: { ...entry().model, slug: "second-model", name: "Second Model" } }),
    ];

    renderQueuePage();
    await screen.findByText("Articulated Dragon");

    fireEvent.click(screen.getByRole("button", { name: "Move Articulated Dragon down" }));

    await waitFor(() => expect(moveMock).toHaveBeenCalledExactlyOnceWith({ entryId: 1, position: 2 }));
  });

  it("disables up on the first entry and down on the last entry", async () => {
    queueDataBox.current = [
      entry({ id: 1, position: 1 }),
      entry({ id: 2, position: 2, model: { ...entry().model, slug: "second-model", name: "Second Model" } }),
    ];

    renderQueuePage();
    await screen.findByText("Second Model");

    expect(screen.getByRole("button", { name: "Move Articulated Dragon up" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move Second Model down" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move Articulated Dragon down" })).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "Move Second Model up" })).not.toBeDisabled();
  });

  it("removing an entry calls the delete mutation with its id", async () => {
    queueDataBox.current = [entry({ id: 7, position: 1 })];

    renderQueuePage();
    await screen.findByText("Articulated Dragon");

    fireEvent.click(screen.getByRole("button", { name: "Remove Articulated Dragon from queue" }));

    await waitFor(() => expect(removeMock).toHaveBeenCalledExactlyOnceWith(7));
  });

  it("gives each entry's Remove button its own accessible name so they aren't ambiguous", async () => {
    // Fix-review F2: two Remove buttons with the bare name "Remove" are
    // indistinguishable to assistive tech / role queries.
    queueDataBox.current = [
      entry({ id: 1, position: 1 }),
      entry({ id: 2, position: 2, model: { ...entry().model, slug: "second-model", name: "Second Model" } }),
    ];

    renderQueuePage();
    await screen.findByText("Second Model");

    expect(screen.getByRole("button", { name: "Remove Articulated Dragon from queue" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove Second Model from queue" })).toBeInTheDocument();
    expect(screen.getAllByText("Remove")).toHaveLength(2);

    fireEvent.click(screen.getByRole("button", { name: "Remove Second Model from queue" }));
    await waitFor(() => expect(removeMock).toHaveBeenCalledExactlyOnceWith(2));
  });
});

// ---------------------------------------------------------------------------
// print readiness pill + Print action (Round 8 Task 3)
// ---------------------------------------------------------------------------

describe("QueuePage print readiness", () => {
  it("shows a Sliced pill for a row with a printable_file", async () => {
    queueDataBox.current = [entry({ printable_file: PRINTABLE_FILE })];

    renderQueuePage();

    expect(await screen.findByText("Sliced")).toBeInTheDocument();
    expect(screen.queryByText("Needs slicing")).not.toBeInTheDocument();
  });

  it("shows a Needs slicing pill for a row without a printable_file", async () => {
    queueDataBox.current = [entry({ printable_file: null })];

    renderQueuePage();

    expect(await screen.findByText("Needs slicing")).toBeInTheDocument();
    expect(screen.queryByText("Sliced")).not.toBeInTheDocument();
  });

  it("shows no Print button for a row that still needs slicing", async () => {
    queueDataBox.current = [entry({ printable_file: null })];

    renderQueuePage();
    await screen.findByText("Needs slicing");

    expect(screen.queryByRole("button", { name: "Print" })).not.toBeInTheDocument();
  });

  it("shows no Print button when the printer feature is disabled, even if sliced", async () => {
    featuresDataBox.current = { printer_enabled: false };
    queueDataBox.current = [entry({ printable_file: PRINTABLE_FILE })];

    renderQueuePage();
    await screen.findByText("Sliced");

    expect(screen.queryByRole("button", { name: "Print" })).not.toBeInTheDocument();
  });

  it("shows no Print button when no printers are configured, even if sliced", async () => {
    printersDataBox.current = [];
    queueDataBox.current = [entry({ printable_file: PRINTABLE_FILE })];

    renderQueuePage();
    await screen.findByText("Sliced");

    expect(screen.queryByRole("button", { name: "Print" })).not.toBeInTheDocument();
  });

  it("shows a Print button when sliced, the feature is on, and a printer exists", async () => {
    queueDataBox.current = [entry({ printable_file: PRINTABLE_FILE })];

    renderQueuePage();

    expect(await screen.findByRole("button", { name: "Print" })).toBeInTheDocument();
  });

  it("clicking Print opens the send-to-printer dialog", async () => {
    queueDataBox.current = [entry({ printable_file: PRINTABLE_FILE })];

    renderQueuePage();
    fireEvent.click(await screen.findByRole("button", { name: "Print" }));

    expect(await screen.findByText(`Send ${PRINTABLE_FILE.rel_path} to a printer`)).toBeInTheDocument();
  });

  it("closing the dialog without sending does not remove the entry or toast", async () => {
    queueDataBox.current = [entry({ id: 9, printable_file: PRINTABLE_FILE })];

    renderQueuePage();
    fireEvent.click(await screen.findByRole("button", { name: "Print" }));
    await screen.findByText(`Send ${PRINTABLE_FILE.rel_path} to a printer`);

    fireEvent.keyDown(document.body, { key: "Escape" });

    await waitFor(() =>
      expect(screen.queryByText(`Send ${PRINTABLE_FILE.rel_path} to a printer`)).not.toBeInTheDocument(),
    );
    expect(removeMock).not.toHaveBeenCalled();
    expect(toastSuccessMock).not.toHaveBeenCalled();
  });

  it("a successful send removes the entry from the queue and toasts", async () => {
    startPrintMock.mockImplementation(
      (_body: unknown, opts?: { onSuccess?: () => void }) => opts?.onSuccess?.(),
    );
    queueDataBox.current = [entry({ id: 9, printable_file: PRINTABLE_FILE })];

    renderQueuePage();
    fireEvent.click(await screen.findByRole("button", { name: "Print" }));
    fireEvent.click(await screen.findByRole("button", { name: "Send to printer" }));

    await waitFor(() => expect(removeMock).toHaveBeenCalledExactlyOnceWith(9));
    expect(toastSuccessMock).toHaveBeenCalledWith("Sent to printer");
  });
});
