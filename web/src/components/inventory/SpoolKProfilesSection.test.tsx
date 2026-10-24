import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SpoolKProfilesSection } from "./SpoolKProfilesSection";

const { getMock, putMock, deleteMock } = vi.hoisted(() => ({ getMock: vi.fn(), putMock: vi.fn(), deleteMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, put: putMock, delete: deleteMock } };
});

const LINK = { printer_id: 1, extruder: 0, nozzle_diameter: "0.4", k_value: 0.03, n_coef: 0, name: "Old", cali_idx: 2, setting_id: "PFold" };
const PROFILE = { cali_idx: 5, setting_id: "PFnew", filament_id: "GFL99", name: "Fresh", k_value: 0.04, n_coef: 0, extruder_id: 0, nozzle_diameter: "0.4" };

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn(() => false);
  [getMock, putMock, deleteMock].forEach((m) => m.mockReset());
  getMock.mockImplementation(async (url: string) => {
    if (url === "/spools/7/kprofiles") return [LINK];
    if (url.startsWith("/printers/2/kprofiles")) return { profiles: [PROFILE], nozzle_diameter: "0.4" };
    if (url.startsWith("/printers")) return [{ id: 1, name: "A1 Mini", capabilities: { kprofiles: true } }, { id: 2, name: "P1S", capabilities: { kprofiles: true } }];
    return [];
  });
});

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SpoolKProfilesSection spoolId={7} canWrite />
    </QueryClientProvider>,
  );
}

describe("SpoolKProfilesSection", () => {
  it("lists links and removes one", async () => {
    deleteMock.mockResolvedValue(undefined);
    wrap();
    expect(await screen.findByText(/A1 Mini/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/spools/7/kprofiles/1?extruder=0&nozzle=0.4"));
  });

  it("adds a profile from a printer, merging with existing links", async () => {
    putMock.mockResolvedValue([]);
    wrap();
    await screen.findByText(/A1 Mini/);
    fireEvent.click(screen.getByRole("button", { name: "Add from printer…" }));
    fireEvent.click(await screen.findByRole("combobox", { name: "Choose a printer" }));
    fireEvent.click(await screen.findByRole("option", { name: "P1S" }));
    fireEvent.click(await screen.findByRole("button", { name: "Add" }));
    await waitFor(() => expect(putMock).toHaveBeenCalled());
    const [url, body] = putMock.mock.calls[0];
    expect(url).toBe("/spools/7/kprofiles");
    expect(body).toEqual([LINK, expect.objectContaining({ printer_id: 2, extruder: 0, nozzle_diameter: "0.4", setting_id: "PFnew", cali_idx: 5, k_value: 0.04 })]);
  });

  it("hides write actions without permission", async () => {
    const qc = new QueryClient();
    render(
      <QueryClientProvider client={qc}>
        <SpoolKProfilesSection spoolId={7} canWrite={false} />
      </QueryClientProvider>,
    );
    await screen.findByText(/A1 Mini/);
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add from printer…" })).toBeNull();
  });
});
