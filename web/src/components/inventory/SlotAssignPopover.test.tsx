import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AmsTray, SpoolOut } from "@/api/types";
import { SlotAssignPopover } from "@/components/inventory/SlotAssignPopover";

const { getMock, putMock, authMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  putMock: vi.fn(),
  authMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, put: putMock } };
});

vi.mock("@/api/auth", () => ({ useAuth: () => authMock() }));

// Radix's Popover never reaches an interactive open state under jsdom --
// same workaround ViewerMorePanel.test.tsx uses: render the content
// unconditionally so its controls are queryable/clickable directly.
vi.mock("@/components/ui/popover", () => ({
  Popover: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  PopoverTrigger: ({ children }: { children?: ReactNode }) => <>{children}</>,
  PopoverContent: ({ children }: { children?: ReactNode }) => <div data-testid="popover-content">{children}</div>,
  PopoverHeader: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  PopoverTitle: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  PopoverDescription: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
}));

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

function tray(overrides: Partial<AmsTray> = {}): AmsTray {
  return {
    slot: 0,
    ams_unit: 0,
    tray: 2,
    color: "#ff0000",
    material: "PLA",
    tray_type: "PLA",
    tray_uuid: "ABC123",
    remain_pct: 10,
    spool: null,
    ...overrides,
  };
}

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
    weight_used_g: 0,
    cost_per_kg: 20,
    location: null,
    low_stock_g: 100,
    tray_uuid: null,
    archived_at: null,
    last_used_at: null,
    created_by_user_id: 1,
    created_at: "2026-01-01T00:00:00Z",
    remaining_g: 1000,
    low_stock: false,
    slot: null,
    ...overrides,
  };
}

function renderPopover(overrides: Partial<AmsTray> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SlotAssignPopover printerId={7} tray={tray(overrides)}>
        <span>chip</span>
      </SlotAssignPopover>
    </QueryClientProvider>,
  );
}

describe("SlotAssignPopover", () => {
  beforeEach(() => {
    getMock.mockReset();
    putMock.mockReset();
    putMock.mockResolvedValue(undefined);
    authMock.mockReturnValue({ can: () => true, canPrinter: () => true });
    getMock.mockImplementation((path: string) => {
      if (path.startsWith("/spools")) return Promise.resolve([spool()]);
      if (path === "/materials") return Promise.resolve([]);
      return Promise.resolve([]);
    });
  });

  it("calls PUT with the tray's ams_unit/tray coordinates when a spool is picked", async () => {
    renderPopover();
    const select = await screen.findByLabelText("Assign spool");
    await screen.findByText(/Red/);
    fireEvent.change(select, { target: { value: "1" } });

    await waitFor(() => expect(putMock).toHaveBeenCalledWith("/printers/7/slots/0/2", { spool_id: 1 }));
  });

  it("sends spool_id: null on Clear", async () => {
    renderPopover();
    await screen.findByLabelText("Assign spool");
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));

    await waitFor(() => expect(putMock).toHaveBeenCalledWith("/printers/7/slots/0/2", { spool_id: null }));
  });

  it("uses the 255/254 external-spool sentinel for an external tray", async () => {
    renderPopover({ ams_unit: 255, tray: 254 });
    await screen.findByLabelText("Assign spool");
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));

    await waitFor(() => expect(putMock).toHaveBeenCalledWith("/printers/7/slots/255/254", { spool_id: null }));
  });
});
