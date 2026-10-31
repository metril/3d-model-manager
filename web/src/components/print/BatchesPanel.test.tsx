import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import type { Batch } from "@/api/types";
import { BatchesPanel } from "@/components/print/BatchesPanel";

const { getMock, postMock, patchMock, deleteMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  patchMock: vi.fn(),
  deleteMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock, patch: patchMock, delete: deleteMock } };
});
vi.mock("@/api/auth", () => ({ useAuth: () => ({ can: () => true }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
// QueuePage pulls in the router; the panel only needs its state chip.
vi.mock("@/pages/QueuePage", () => ({ QueueStateChip: ({ state }: { state: string }) => <span>{state}</span> }));

function batch(o: Partial<Batch> = {}): Batch {
  return {
    id: 1,
    name: "Gears",
    model_id: 5,
    file_id: null,
    recipe_id: null,
    printer_id: null,
    quantity: 10,
    status: "draft",
    due_date: "2026-11-01",
    notes: "",
    plate_targets: {},
    cost_center_id: null,
    created_by_user_id: 1,
    created_at: "2026-10-24T00:00:00Z",
    progress: { total: 10, done: 4, failed: 1, skipped: 2, waiting: 3 },
    ...o,
  };
}

let batches: Batch[] = [];

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <BatchesPanel />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  batches = [batch()];
  getMock.mockReset().mockImplementation((path: string) => {
    if (path === "/batches") return Promise.resolve(batches);
    if (path === "/printers") return Promise.resolve([{ id: 2, name: "Printer A" }]);
    if (path.startsWith("/models?")) return Promise.resolve({ items: [{ id: 5, name: "Gear set" }], next_cursor: null });
    if (path.startsWith("/queue")) return Promise.resolve([]);
    return Promise.resolve({});
  });
  postMock.mockReset().mockImplementation((path: string) => Promise.resolve(path.endsWith("/dispatch") ? batch({ status: "active" }) : batch()));
  patchMock.mockReset().mockResolvedValue(batch());
  deleteMock.mockReset().mockResolvedValue(undefined);
});

describe("BatchesPanel", () => {
  it("renders the table with a progress bar summarising done/failed/skipped/waiting", async () => {
    renderPanel();
    expect(await screen.findByText("Gears")).toBeInTheDocument();
    const bar = screen.getByRole("img", { name: "4 done, 1 failed, 2 skipped, 3 waiting of 10" });
    expect(bar.querySelectorAll("[data-seg]")).toHaveLength(3);
    expect(screen.getByText("2026-11-01")).toBeInTheDocument();
  });

  it("creates a batch with the exact payload, then dispatches it", async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Create batch" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Run B" } });
    fireEvent.change(within(dialog).getByPlaceholderText("Search models..."), { target: { value: "gear" } });
    fireEvent.click(await within(dialog).findByRole("button", { name: "Gear set" }));
    fireEvent.change(within(dialog).getByLabelText("Quantity", { selector: "#batch-qty" }), { target: { value: "6" } });
    fireEvent.change(within(dialog).getByLabelText("Printer"), { target: { value: "2" } });
    fireEvent.change(within(dialog).getByLabelText("Due date"), { target: { value: "2026-12-01" } });
    fireEvent.click(within(dialog).getByRole("switch", { name: "Manual start" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/batches", {
        name: "Run B",
        model_id: 5,
        quantity: 6,
        printer_id: 2,
        due_date: "2026-12-01",
        notes: "",
        plate_targets: {},
        manual_start: true,
      }),
    );

    fireEvent.click(await screen.findByRole("button", { name: "Dispatch" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/batches/1/dispatch"));
  });

  it("confirms before re-dispatching with force after a does_not_fit refusal", async () => {
    postMock.mockImplementation((path: string, body?: unknown) =>
      path.endsWith("/dispatch") && !body
        ? Promise.reject(new ApiError(422, "part does not fit", "does_not_fit", [{ id: 9, name: "big.stl" }]))
        : Promise.resolve(batch({ status: "active" })),
    );
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Dispatch" }));
    const dlg = await screen.findByRole("dialog");
    expect(within(dlg).getByText(/1 of 1 parts don't fit the bed: big\.stl/)).toBeInTheDocument();
    fireEvent.click(within(dlg).getByRole("button", { name: "Queue anyway" }));
    await waitFor(() => expect(postMock).toHaveBeenLastCalledWith("/batches/1/dispatch", { force: true }));
  });

  it("ungroups an active batch after confirming", async () => {
    batches = [batch({ status: "active" })];
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Ungroup" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Ungroup" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/batches/1/ungroup"));
  });

  it("deletes after confirming", async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/batches/1"));
  });

  it("opens the detail drawer filtered by batch_id", async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Gears" }));
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/queue?include_done=true&batch_id=1"));
  });
});
