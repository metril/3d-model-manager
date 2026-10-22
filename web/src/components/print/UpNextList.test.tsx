import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FileOut, PrinterOut, QueueEntry } from "@/api/types";
import { UpNextList } from "@/components/print/UpNextList";

vi.mock("@/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/auth")>()),
  useAuth: () => ({ data: { id: 1, username: "tester" }, can: () => true, canPrinter: () => true }),
}));

const { moveMock, printersDataBox } = vi.hoisted(() => ({
  moveMock: vi.fn(),
  printersDataBox: { current: [] as PrinterOut[] },
}));

vi.mock("@/api/queue", () => ({
  useMoveQueueEntry: () => ({ mutate: moveMock, isPending: false }),
}));

vi.mock("@/api/printers", () => ({
  usePrinters: () => ({ data: printersDataBox.current }),
}));

// `PrintDialog` is unit-tested on its own (`PrintDialog.test.tsx`) -- here
// it's stubbed to a visible marker when open, so this file only asserts
// UpNextList's OWN logic: when the Print… button shows, and that it opens
// the dialog with the right model source.
vi.mock("@/components/print/PrintDialog", () => ({
  PrintDialog: ({ open, source }: { open: boolean; source: { kind: string; modelId?: number } }) =>
    open ? <div data-testid="print-dialog">{`${source.kind}:${source.modelId}`}</div> : null,
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

function renderList(entries: QueueEntry[], printerId?: number) {
  const rootRoute = createRootRoute();
  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => <UpNextList entries={entries} printerId={printerId} />,
  });
  const modelRoute = createRoute({ getParentRoute: () => rootRoute, path: "/models/$slug", component: () => null });
  const queueRoute = createRoute({ getParentRoute: () => rootRoute, path: "/print/queue", component: () => null });
  const router = createRouter({
    routeTree: rootRoute.addChildren([indexRoute, modelRoute, queueRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  return render(<RouterProvider router={router} />);
}

beforeEach(() => {
  moveMock.mockClear();
  printersDataBox.current = [PRINTER];
});

describe("UpNextList", () => {
  it("shows an empty state when there's nothing queued", async () => {
    renderList([]);
    expect(await screen.findByText("Nothing queued.")).toBeInTheDocument();
  });

  it("shows a printer-scoped empty state and marker when given a printerId", async () => {
    renderList([], 1);
    expect(await screen.findByText("Nothing queued for this printer.")).toBeInTheDocument();
    expect(document.querySelector('[data-printer-id="1"]')).toBeInTheDocument();
  });

  it("links to the full queue", async () => {
    renderList([entry()]);
    const link = await screen.findByRole("link", { name: "All queue" });
    expect(link).toHaveAttribute("href", "/print/queue");
  });

  it("moving up calls move with position - 1", async () => {
    renderList([entry({ id: 2, position: 2 })]);
    fireEvent.click(await screen.findByRole("button", { name: "Move Articulated Dragon up" }));
    await waitFor(() => expect(moveMock).toHaveBeenCalledExactlyOnceWith({ entryId: 2, position: 1 }));
  });

  it("shows no Print… button without a printable_file", async () => {
    renderList([entry({ printable_file: null })]);
    await screen.findByText("Articulated Dragon");
    expect(screen.queryByRole("button", { name: "Print…" })).not.toBeInTheDocument();
  });

  it("shows no Print… button when no printer is printable", async () => {
    printersDataBox.current = [];
    renderList([entry({ printable_file: PRINTABLE_FILE })]);
    await screen.findByText("Articulated Dragon");
    expect(screen.queryByRole("button", { name: "Print…" })).not.toBeInTheDocument();
  });

  it("opens the PrintDialog with this entry's model when printable", async () => {
    renderList([entry({ printable_file: PRINTABLE_FILE })]);
    fireEvent.click(await screen.findByRole("button", { name: "Print…" }));
    expect(await screen.findByTestId("print-dialog")).toHaveTextContent("model:1");
  });
});
