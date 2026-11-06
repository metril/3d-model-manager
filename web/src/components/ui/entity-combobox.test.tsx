import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { EntityCombobox, ServiceCombobox } from "@/components/ui/entity-combobox";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock } };
});

// cmdk needs ResizeObserver and scrollIntoView, which jsdom lacks.
beforeAll(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  Element.prototype.scrollIntoView = vi.fn();
});
afterAll(() => vi.unstubAllGlobals());

const ENTITIES = [
  { entity_id: "sensor.plug_power", friendly_name: "Plug power", device_class: "power", unit: "W", state: "120" },
  { entity_id: "sensor.price", friendly_name: "Price", device_class: "monetary", unit: "EUR/kWh", state: "0.3" },
];

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("EntityCombobox", () => {
  beforeEach(() => {
    getMock.mockReset();
    getMock.mockResolvedValue(ENTITIES);
  });

  it("requests the domain and device class filters", async () => {
    wrap(<EntityCombobox value="" onChange={vi.fn()} ariaLabel="Entity" domain={["sensor"]} deviceClass={["power", "energy"]} />);
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/ha/entities?domain=sensor&device_class=power%2Cenergy"));
  });

  it("lists name, id and state then picks an option", async () => {
    const onChange = vi.fn();
    wrap(<EntityCombobox value="" onChange={onChange} ariaLabel="Entity" />);
    await waitFor(() => expect(getMock).toHaveBeenCalled());
    fireEvent.click(screen.getByLabelText("Entity"));
    expect(await screen.findByText("Plug power")).toBeInTheDocument();
    expect(screen.getByText("sensor.plug_power")).toBeInTheDocument();
    expect(screen.getByText("120 W")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Plug power"));
    expect(onChange).toHaveBeenCalledWith("sensor.plug_power");
  });

  it("applies the client-side filter prop", async () => {
    wrap(<EntityCombobox value="" onChange={vi.fn()} ariaLabel="Entity" filter={(e) => e.device_class === "monetary"} />);
    await waitFor(() => expect(getMock).toHaveBeenCalled());
    fireEvent.click(screen.getByLabelText("Entity"));
    expect(await screen.findByText("Price")).toBeInTheDocument();
    expect(screen.queryByText("Plug power")).not.toBeInTheDocument();
  });

  it("accepts typed free text when HA is unavailable", async () => {
    getMock.mockRejectedValue(new Error("503"));
    const onChange = vi.fn();
    wrap(<EntityCombobox value="" onChange={onChange} ariaLabel="Entity" />);
    await waitFor(() => expect(getMock).toHaveBeenCalled());
    fireEvent.click(screen.getByLabelText("Entity"));
    fireEvent.change(await screen.findByPlaceholderText("Search entities…"), { target: { value: "sensor.custom" } });
    fireEvent.click(await screen.findByText('Use "sensor.custom"'));
    expect(onChange).toHaveBeenCalledWith("sensor.custom");
  });
});

describe("ServiceCombobox", () => {
  it("lists notify services", async () => {
    getMock.mockReset();
    getMock.mockResolvedValue(["notify.mobile_app_phone"]);
    const onChange = vi.fn();
    wrap(<ServiceCombobox domain="notify" value="" onChange={onChange} ariaLabel="Service" />);
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/ha/services?domain=notify"));
    fireEvent.click(screen.getByLabelText("Service"));
    fireEvent.click(await screen.findByText("notify.mobile_app_phone"));
    expect(onChange).toHaveBeenCalledWith("notify.mobile_app_phone");
  });
});
