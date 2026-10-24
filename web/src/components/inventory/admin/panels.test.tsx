import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ColorCatalogPanel } from "@/components/inventory/admin/ColorCatalogPanel";
import { LocationsPanel } from "@/components/inventory/admin/LocationsPanel";
import { SuppliersPanel } from "@/components/inventory/admin/SuppliersPanel";

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
vi.mock("@/api/auth", () => ({ useAuth: () => ({ can: () => true, canPrinter: () => true }) }));
vi.mock("@/components/ui/select", () => import("@/test/selectMock"));

function wrap(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

const supplier = { id: 1, name: "Acme", website: "https://acme.test", customer_number: "C-1", note: null, created_at: "" };
const location = { id: 4, name: "Dry box", identifier: "DB1", created_at: "" };
const sensor = {
  id: 8, location_id: 4, name: "Humidity", entity_id: "sensor.dry_h", kind: "humidity", device_class: "humidity", unit: "%",
  alert_above: 30, alert_below: null, notify_on_alert: true, show_on_card: true, sort_order: 0,
  last_state: "21", last_changed_at: null, alerting: false,
};
const color = { id: 3, manufacturer: "Bambu Lab", color_name: "Jade White", hex: "#FFFFFF", material: "PLA", is_default: true, extra_colors: [], effect_type: null };

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  [getMock, postMock, patchMock, deleteMock].forEach((m) => m.mockReset());
  postMock.mockResolvedValue({});
  patchMock.mockResolvedValue({});
  deleteMock.mockResolvedValue(undefined);
});

describe("SuppliersPanel", () => {
  beforeEach(() => getMock.mockResolvedValue([supplier]));

  it("lists, creates, edits and deletes", async () => {
    wrap(<SuppliersPanel />);
    expect(await screen.findByText("Acme")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Add supplier" }));
    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Beta" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/suppliers", { name: "Beta", website: null, customer_number: null, note: null }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Edit Acme" }));
    fireEvent.change(await screen.findByLabelText("Customer no."), { target: { value: "C-2" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(patchMock).toHaveBeenCalledWith("/suppliers/1", expect.objectContaining({ customer_number: "C-2" })));

    fireEvent.click(screen.getByRole("button", { name: "Delete Acme" }));
    fireEvent.click(await screen.findByRole("button", { name: /^Delete$/ }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/suppliers/1"));
  });
});

describe("LocationsPanel", () => {
  beforeEach(() => {
    getMock.mockImplementation((path: string) => {
      if (path === "/storage-locations") return Promise.resolve([location]);
      if (path === "/storage-locations/4/sensors") return Promise.resolve([sensor]);
      if (path === "/storage-locations/4/readings")
        return Promise.resolve([{ sensor_id: 8, state: "35", unit: "%", alerting: true, last_changed_at: null }]);
      if (path === "/storage-locations/sensors/entities")
        return Promise.resolve([{ entity_id: "sensor.temp", name: "Temp", device_class: "temperature", unit: "C", state: "20" }]);
      return Promise.resolve([]);
    });
  });

  it("creates a location", async () => {
    wrap(<LocationsPanel />);
    await screen.findByText("Dry box");
    fireEvent.click(screen.getByRole("button", { name: "Add location" }));
    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Shelf B" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/storage-locations", { name: "Shelf B", identifier: null }));
  });

  it("shows live readings with an alert badge and adds a sensor from HA entities", async () => {
    wrap(<LocationsPanel />);
    await screen.findByText("Dry box");
    fireEvent.click(screen.getByRole("button", { name: "Sensors for Dry box" }));
    expect(await screen.findByText("Humidity")).toBeInTheDocument();
    expect(await screen.findByText("35%")).toBeInTheDocument();
    expect(screen.getByText("Alert")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Add sensor" }));
    fireEvent.change(await screen.findByLabelText("Home Assistant entity"), { target: { value: "sensor.temp" } });
    fireEvent.change(screen.getByLabelText("Alert above"), { target: { value: "28" } });
    fireEvent.click(screen.getByRole("switch", { name: "Notify on alert" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/storage-locations/4/sensors", {
        name: "Temp", entity_id: "sensor.temp", kind: "temperature", alert_above: 28, alert_below: null,
        notify_on_alert: true, show_on_card: true,
      }),
    );
  });

  it("deletes a location after confirming", async () => {
    wrap(<LocationsPanel />);
    await screen.findByText("Dry box");
    fireEvent.click(screen.getByRole("button", { name: "Delete Dry box" }));
    fireEvent.click(await screen.findByRole("button", { name: /^Delete$/ }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/storage-locations/4"));
  });
});

describe("ColorCatalogPanel", () => {
  beforeEach(() => getMock.mockResolvedValue([color, { ...color, id: 4, manufacturer: "Prusament", color_name: "Galaxy Black" }]));

  it("filters, adds, and resets defaults after confirmation", async () => {
    wrap(<ColorCatalogPanel />);
    expect(await screen.findByText("Jade White")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Filter colours"), { target: { value: "galaxy" } });
    expect(screen.queryByText("Jade White")).toBeNull();
    expect(screen.getByText("Galaxy Black")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Add colour" }));
    fireEvent.change(await screen.findByLabelText("Manufacturer"), { target: { value: "Acme" } });
    fireEvent.change(screen.getByLabelText("Colour name"), { target: { value: "Teal" } });
    fireEvent.change(screen.getByLabelText("Hex"), { target: { value: "#008080" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/color-catalog", {
        manufacturer: "Acme", color_name: "Teal", hex: "#008080", material: null, effect_type: null, extra_colors: [],
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Reset defaults" }));
    expect(postMock).not.toHaveBeenCalledWith("/color-catalog/reset");
    const confirm = (await screen.findAllByRole("button", { name: "Reset defaults" })).at(-1)!;
    fireEvent.click(confirm);
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/color-catalog/reset"));
  });
});
