import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PrinterPicker } from "@/components/slice/PrinterPicker";
import { SliceDialog } from "@/components/slice/SliceDialog";
import { filterByName, isLoadedTray, sortPresets, splitOnline } from "@/lib/presetList";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock } };
});
vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-router")>()),
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}));

function wrap(node: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

function overview(onlineIds: number[], all: number[]) {
  return {
    printers: all.map((id) => ({ printer: { id, name: `P${id}` }, status: { online: onlineIds.includes(id) } })),
    up_next: [],
    recent_runs: [],
  };
}

beforeEach(() => getMock.mockReset());

describe("presetList helpers", () => {
  it("filters by name, case-insensitively", () => {
    const items = [{ name: "A1 PLA" }, { name: "A1 PETG" }];
    expect(filterByName(items, "pet")).toEqual([{ name: "A1 PETG" }]);
    expect(filterByName(items, "  ")).toHaveLength(2);
  });

  it("floats compatible rows up, then natural name order", () => {
    const out = sortPresets([
      { name: "PA12", compatible: true },
      { name: "Z", compatible: false },
      { name: "PA6", compatible: true },
    ]);
    expect(out.map((r) => r.name)).toEqual(["PA6", "PA12", "Z"]);
  });

  it("splits online/offline and treats unknown liveness as online", () => {
    const ps = [{ id: 1 }, { id: 2 }];
    expect(splitOnline(ps, new Set([2]))).toEqual({ online: [{ id: 2 }], offline: [{ id: 1 }] });
    expect(splitOnline(ps, null).online).toHaveLength(2);
  });

  it("counts a tray with a material, colour or spool as loaded", () => {
    expect(isLoadedTray({ material: "PLA" })).toBe(true);
    expect(isLoadedTray({ material: null, color: null, spool: null })).toBe(false);
  });
});

describe("PrinterPicker", () => {
  it("lists online printers and hides the rest under an Offline (N) disclosure", async () => {
    getMock.mockResolvedValue(overview([1, 3], [1, 2, 3]));
    wrap(
      <PrinterPicker
        idPrefix="t"
        value={1}
        onChange={vi.fn()}
        printers={[1, 2, 3].map((id) => ({ id, name: `P${id}` }))}
      />,
    );
    await screen.findByText("Offline (1)");
    expect(screen.getByLabelText("P1")).toBeInTheDocument();
    expect(screen.getByLabelText("P3")).toBeInTheDocument();
    // P2 lives inside the closed <details>
    expect(screen.getByLabelText("P2").closest("details")).not.toBeNull();
  });

  it("auto-selects the single online printer", async () => {
    getMock.mockResolvedValue(overview([2], [1, 2]));
    const onChange = vi.fn();
    wrap(
      <PrinterPicker
        idPrefix="t"
        value={null}
        onChange={onChange}
        printers={[1, 2].map((id) => ({ id, name: `P${id}` }))}
      />,
    );
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(2, true));
  });
});

describe("SliceDialog search", () => {
  it("filters recipes by the debounced search box", async () => {
    getMock.mockImplementation((path?: string) => {
      if (path?.startsWith("/recipes"))
        return Promise.resolve([
          { id: 3, name: "A1 PLA", compatible: true },
          { id: 4, name: "A1 PETG", compatible: true },
        ]);
      if (path === "/slicer/health") return Promise.resolve({ configured: true, ok: true });
      return Promise.resolve(null);
    });
    wrap(<SliceDialog open onOpenChange={vi.fn()} file={{ id: 5, rel_path: "a/cube.stl" }} />);
    await screen.findByLabelText("A1 PLA");
    fireEvent.change(screen.getByLabelText("Search recipes"), { target: { value: "petg" } });
    await waitFor(() => expect(screen.queryByLabelText("A1 PLA")).toBeNull());
    expect(screen.getByLabelText("A1 PETG")).toBeInTheDocument();
  });
});
