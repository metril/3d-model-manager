import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SpoolOut } from "@/api/types";
import { SpoolTable } from "@/components/inventory/SpoolTable";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock } };
});

vi.mock("@/components/ui/select", () => ({
  Select: ({
    value,
    onValueChange,
    children,
    ...rest
  }: {
    value?: string;
    onValueChange: (value: string) => void;
    children?: ReactNode;
  } & Record<string, unknown>) => (
    <select value={value} onChange={(event) => onValueChange(event.target.value)} {...rest}>
      {children}
    </select>
  ),
  SelectTrigger: () => null,
  SelectValue: () => null,
  SelectContent: ({ children }: { children?: ReactNode }) => <>{children}</>,
  SelectItem: ({ value, children }: { value: string; children?: ReactNode }) => (
    <option value={value}>{children}</option>
  ),
}));

function spool(overrides: Partial<SpoolOut> = {}): SpoolOut {
  return {
    id: 1,
    material_id: null,
    material_type: "PLA",
    brand: "Bambu",
    color_hex: "#ff0000",
    color_name: "Red",
    label_weight_g: 1000,
    empty_weight_g: 250,
    weight_used_g: 900,
    cost_per_kg: 20,
    location: "Shelf A",
    low_stock_g: 100,
    tray_uuid: null,
    archived_at: null,
    last_used_at: "2026-10-20T00:00:00Z",
    created_by_user_id: 1,
    created_at: "2026-01-01T00:00:00Z",
    remaining_g: 100,
    low_stock: true,
    slot: null,
    ...overrides,
  };
}

function renderTable(onOpen = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SpoolTable onOpen={onOpen} />
    </QueryClientProvider>,
  );
  return onOpen;
}

describe("SpoolTable", () => {
  beforeEach(() => {
    getMock.mockReset();
    getMock.mockImplementation((path: string) => {
      if (path.startsWith("/spools")) return Promise.resolve([spool()]);
      if (path === "/printers") return Promise.resolve([]);
      return Promise.resolve([]);
    });
  });

  it("renders remaining grams and a low-stock badge", async () => {
    renderTable();
    expect(await screen.findByText("100 g")).toBeInTheDocument();
    expect(screen.getByText("Low")).toBeInTheDocument();
  });

  it("narrows to low-stock spools when the Low stock filter is checked", async () => {
    renderTable();
    await screen.findByText("100 g");

    const checkbox = screen.getByLabelText("Low stock");
    fireEvent.click(checkbox);

    await waitFor(() => {
      const call = getMock.mock.calls.find((c) => typeof c[0] === "string" && c[0].includes("low_stock=true"));
      expect(call).toBeTruthy();
    });
  });

  it("opens the detail sheet on row click", async () => {
    const onOpen = vi.fn();
    renderTable(onOpen);
    const row = await screen.findByText("Red");
    fireEvent.click(row);
    expect(onOpen).toHaveBeenCalledWith(spool());
  });
});
