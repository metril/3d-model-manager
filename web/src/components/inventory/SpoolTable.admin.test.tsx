import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SpoolOut } from "@/api/types";
import { SpoolTable } from "@/components/inventory/SpoolTable";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock } };
});
vi.mock("@/api/auth", () => ({ useAuth: () => ({ can: () => true, canPrinter: () => true }) }));
vi.mock("@/components/ui/select", () => import("@/test/selectMock"));

function spool(id: number, color: string): SpoolOut {
  return {
    id, material_id: null, material_type: "PLA", brand: "Bambu", color_hex: "#ff0000", color_name: color,
    label_weight_g: 1000, empty_weight_g: 250, weight_used_g: 100, cost_per_kg: 20, location: "Shelf A", location_id: 3,
    low_stock_g: 100, tray_uuid: null, archived_at: null, last_used_at: null, created_by_user_id: 1,
    created_at: "2026-01-01T00:00:00Z", remaining_g: 900, low_stock: false, slot: null,
  };
}

function renderTable() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SpoolTable onOpen={vi.fn()} />
    </QueryClientProvider>,
  );
}

describe("SpoolTable selection toolbar", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    getMock.mockImplementation((path: string) => {
      if (path.startsWith("/spools")) return Promise.resolve([spool(1, "Red"), spool(2, "Blue")]);
      if (path === "/storage-locations") return Promise.resolve([{ id: 3, name: "Shelf A", identifier: null, created_at: "" }]);
      return Promise.resolve([]);
    });
  });

  it("shows no toolbar until a row is selected, then header select-all selects both", async () => {
    renderTable();
    await screen.findByText("Red");
    expect(screen.queryByRole("toolbar")).toBeNull();
    fireEvent.click(screen.getByLabelText("Select PLA Red"));
    expect(screen.getByText("1 selected")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Select all spools"));
    expect(screen.getByText("2 selected")).toBeInTheDocument();
  });

  it("bulk edit sends only the toggled fields", async () => {
    postMock.mockResolvedValue({ results: [{ id: 1, ok: true }], not_found: [] });
    renderTable();
    await screen.findByText("Red");
    fireEvent.click(screen.getByLabelText("Select PLA Red"));
    fireEvent.click(screen.getByRole("button", { name: "Bulk edit" }));
    fireEvent.change(await screen.findByLabelText("Cost / kg"), { target: { value: "25" } });
    fireEvent.click(screen.getByRole("button", { name: "Update 1 spool" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/spools/bulk-update", { ids: [1], update: { cost_per_kg: 25 } }));
  });

  it("renders per-id errors from a partial bulk update", async () => {
    postMock.mockResolvedValue({ results: [{ id: 1, ok: false, error: "bad hex" }], not_found: [] });
    renderTable();
    await screen.findByText("Red");
    fireEvent.click(screen.getByLabelText("Select PLA Red"));
    fireEvent.click(screen.getByRole("button", { name: "Bulk edit" }));
    fireEvent.change(await screen.findByLabelText("Brand"), { target: { value: "X" } });
    fireEvent.click(screen.getByRole("button", { name: "Update 1 spool" }));
    expect(await screen.findByText("#1: bad hex")).toBeInTheDocument();
  });

  it("delete asks for confirmation with the count before posting", async () => {
    postMock.mockResolvedValue(undefined);
    renderTable();
    await screen.findByText("Red");
    fireEvent.click(screen.getByLabelText("Select all spools"));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("Delete 2 spools?")).toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalled();
    const confirm = screen.getAllByRole("button", { name: "Delete" }).at(-1)!;
    fireEvent.click(confirm);
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/spools/bulk-delete", { ids: [1, 2] }));
  });

  it("archive posts bulk-archive for the selection", async () => {
    postMock.mockResolvedValue(undefined);
    renderTable();
    await screen.findByText("Red");
    fireEvent.click(screen.getByLabelText("Select PLA Blue"));
    fireEvent.click(screen.getByRole("button", { name: "Archive" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/spools/bulk-archive", { ids: [2] }));
  });
});
