import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { VirtualPrinter } from "@/api/types";
import { VirtualPrinterCard } from "@/components/settings/VirtualPrinterCard";

const { getMock, postMock, patchMock, deleteMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  patchMock: vi.fn(),
  deleteMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock, post: postMock, patch: patchMock, delete: deleteMock },
  };
});

function fakeVp(overrides: Partial<VirtualPrinter> = {}): VirtualPrinter {
  return {
    id: 1,
    name: "Virtual A1 Mini",
    model: "N1",
    serial: "03000A123456789",
    access_code: "48151623",
    mode: "queue",
    target_printer_id: null,
    enabled: true,
    created_by_user_id: 1,
    created_at: "2026-10-23T00:00:00Z",
    updated_at: "2026-10-23T00:00:00Z",
    ...overrides,
  };
}

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <VirtualPrinterCard />
    </QueryClientProvider>,
  );
}

describe("VirtualPrinterCard", () => {
  let rows: VirtualPrinter[];

  beforeEach(() => {
    rows = [];
    for (const m of [getMock, postMock, patchMock, deleteMock]) m.mockReset();
    getMock.mockImplementation((path: string) => {
      if (path === "/virtual-printers") return Promise.resolve(rows);
      if (path === "/printers") return Promise.resolve([{ id: 7, name: "Real A1 Mini" }]);
      return Promise.resolve({});
    });
  });

  it("shows the serial and access code of each virtual printer", async () => {
    rows = [fakeVp()];
    renderCard();
    expect(await screen.findByText("03000A123456789")).toBeInTheDocument();
    expect(screen.getByLabelText("Access code for Virtual A1 Mini")).toHaveTextContent("48151623");
    expect(screen.getByText(/In Bambu Studio/)).toBeInTheDocument();
  });

  it("creates a virtual printer and lists it", async () => {
    postMock.mockImplementation((_p: string, body: { name: string }) => {
      rows = [fakeVp({ name: body.name })];
      return Promise.resolve(rows[0]);
    });
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Add virtual printer" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Shop VP" } });
    fireEvent.click(screen.getByRole("button", { name: "Create virtual printer" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith(
        "/virtual-printers",
        expect.objectContaining({ name: "Shop VP", model: "N1", mode: "queue", target_printer_id: null }),
      ),
    );
    expect(await screen.findByText("Shop VP")).toBeInTheDocument();
  });

  it("requires a target printer in proxy mode and sends it", async () => {
    postMock.mockResolvedValue(fakeVp());
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Add virtual printer" }));
    fireEvent.change(screen.getByLabelText("When a slicer sends a print"), { target: { value: "proxy" } });
    const submit = screen.getByRole("button", { name: "Create virtual printer" });
    expect(submit).toBeDisabled();
    await screen.findByRole("option", { name: "Real A1 Mini" });
    fireEvent.change(screen.getByLabelText("Target printer"), { target: { value: "7" } });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith(
        "/virtual-printers",
        expect.objectContaining({ mode: "proxy", target_printer_id: 7 }),
      ),
    );
  });

  it("regenerates the access code via PATCH", async () => {
    rows = [fakeVp()];
    patchMock.mockResolvedValue(fakeVp({ access_code: "11112222" }));
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Regenerate" }));
    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledWith("/virtual-printers/1", { regenerate_access_code: true }),
    );
  });

  it("deletes only after a second confirming click", async () => {
    rows = [fakeVp()];
    deleteMock.mockImplementation(() => {
      rows = [];
      return Promise.resolve(undefined);
    });
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(deleteMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/virtual-printers/1"));
    expect(await screen.findByText("No virtual printers yet.")).toBeInTheDocument();
  });
});
