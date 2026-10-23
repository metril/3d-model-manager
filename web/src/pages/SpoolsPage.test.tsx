import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SpoolOut } from "@/api/types";
import { SpoolsPage } from "@/pages/SpoolsPage";

const { getMock, postMock, authMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  authMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock } };
});

vi.mock("@/api/auth", () => ({ useAuth: () => authMock() }));

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
    last_used_at: null,
    created_by_user_id: 1,
    created_at: "2026-01-01T00:00:00Z",
    remaining_g: 100,
    low_stock: true,
    slot: null,
    ...overrides,
  };
}

function renderPage(initialEntries: string[] = ["/inventory/spools"]) {
  const rootRoute = createRootRoute();
  const authenticatedRoute = createRoute({ id: "authenticated", getParentRoute: () => rootRoute });
  const spoolsRoute = createRoute({
    getParentRoute: () => authenticatedRoute,
    path: "/inventory/spools",
    validateSearch: (search: Record<string, unknown>): { low?: string } => ({
      low: search.low != null ? String(search.low) : undefined,
    }),
    component: SpoolsPage,
  });
  const materialsRoute = createRoute({
    getParentRoute: () => authenticatedRoute,
    path: "/inventory/materials",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([authenticatedRoute.addChildren([spoolsRoute, materialsRoute])]),
    history: createMemoryHistory({ initialEntries }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("SpoolsPage", () => {
  beforeEach(() => {
    getMock.mockReset();
    getMock.mockImplementation((path: string) => {
      if (path.startsWith("/spools")) return Promise.resolve([spool()]);
      if (path === "/printers") return Promise.resolve([]);
      if (path === "/materials") return Promise.resolve([]);
      return Promise.resolve([]);
    });
    authMock.mockReturnValue({ can: () => true, canPrinter: () => true });
  });

  it("renders the spool table with the Add spool button for a writer", async () => {
    renderPage();
    expect(await screen.findByText("100 g")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add spool" })).toBeInTheDocument();
  });

  it("hides Add spool for a viewer", async () => {
    authMock.mockReturnValue({ can: () => false, canPrinter: () => true });
    renderPage();
    await screen.findByText("100 g");
    expect(screen.queryByRole("button", { name: "Add spool" })).toBeNull();
  });

  it("narrows to low stock when landing with ?low=1", async () => {
    renderPage(["/inventory/spools?low=1"]);
    await screen.findByText("100 g");
    const call = getMock.mock.calls.find((c) => typeof c[0] === "string" && c[0].includes("low_stock=true"));
    expect(call).toBeTruthy();
  });

  it("shows a Spoolman badge on linked spools", async () => {
    getMock.mockImplementation((path: string) =>
      path.startsWith("/spools") ? Promise.resolve([spool({ spoolman_id: 12 })]) : Promise.resolve([]),
    );
    renderPage();
    expect(await screen.findByText("Spoolman")).toBeInTheDocument();
  });

  it("links an unlinked spool to a picked Spoolman spool", async () => {
    postMock.mockResolvedValue(undefined);
    getMock.mockImplementation((path: string) => {
      if (path === "/spoolman/spools")
        return Promise.resolve([
          { id: 5, name: "Red", material: "PLA", vendor: "Acme", color_hex: null, remaining_weight: 640, used_weight: 360, location: null },
        ]);
      if (path === "/spools/1")
        return Promise.resolve({ ...spool(), usage: [], series: [] });
      if (path.startsWith("/spools")) return Promise.resolve([spool()]);
      return Promise.resolve([]);
    });
    renderPage();
    fireEvent.click(await screen.findByText("100 g"));
    fireEvent.click(await screen.findByRole("button", { name: "Link to Spoolman" }));
    const pick = await screen.findByLabelText("Spoolman spool");
    await screen.findByText(/#5 Acme PLA Red/);
    fireEvent.change(pick, { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Link" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/spoolman/link", { spool_id: 1, spoolman_id: 5 }),
    );
  });

  it("offers Unlink for a linked spool", async () => {
    postMock.mockResolvedValue(undefined);
    getMock.mockImplementation((path: string) => {
      if (path === "/spools/1")
        return Promise.resolve({ ...spool({ spoolman_id: 12 }), usage: [], series: [] });
      if (path.startsWith("/spools")) return Promise.resolve([spool({ spoolman_id: 12 })]);
      return Promise.resolve([]);
    });
    renderPage();
    fireEvent.click(await screen.findByText("100 g"));
    fireEvent.click(await screen.findByRole("button", { name: "Unlink" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/spoolman/unlink", { spool_id: 1 }));
  });
});
