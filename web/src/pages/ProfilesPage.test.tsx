import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PrintRecipe, SlicerProfile } from "@/api/types";
import { ProfilesPage } from "@/pages/ProfilesPage";

const { getMock, postMock, postFormMock, authMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  postFormMock: vi.fn(),
  authMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock, post: postMock, postForm: postFormMock },
  };
});

vi.mock("@/api/auth", () => ({ useAuth: () => authMock() }));

// Radix Tabs switches on focus/pointer, not a bare `fireEvent.click`, so tab
// activation is unreliable under jsdom -- mocked the same way
// `CollectionsPage.test.tsx` does, as a plain controlled click handler.
vi.mock("@/components/ui/tabs", async () => {
  const React = await vi.importActual<typeof import("react")>("react");
  const TabsCtx = React.createContext<{ value: string; setValue: (value: string) => void }>({
    value: "",
    setValue: () => {},
  });
  return {
    Tabs: ({
      value,
      onValueChange,
      children,
    }: { value: string; onValueChange: (value: string) => void; children?: ReactNode }) => (
      <TabsCtx.Provider value={{ value, setValue: onValueChange }}>{children}</TabsCtx.Provider>
    ),
    TabsList: ({ children }: { children?: ReactNode }) => <div role="tablist">{children}</div>,
    TabsTrigger: ({ value, children }: { value: string; children?: ReactNode }) => {
      const ctx = React.useContext(TabsCtx);
      return (
        <button role="tab" type="button" aria-selected={ctx.value === value} onClick={() => ctx.setValue(value)}>
          {children}
        </button>
      );
    },
    TabsContent: ({ value, children }: { value: string; children?: ReactNode }) => {
      const ctx = React.useContext(TabsCtx);
      return ctx.value === value ? <div>{children}</div> : null;
    },
  };
});

// Radix Checkbox's size tracking needs `ResizeObserver`, unpolyfilled in
// jsdom -- plain checkbox input instead, same reasoning as the Select mock.
vi.mock("@/components/ui/checkbox", () => ({
  Checkbox: ({
    checked,
    onCheckedChange,
    ...rest
  }: { checked?: boolean; onCheckedChange: (checked: boolean) => void } & Record<string, unknown>) => (
    <input
      type="checkbox"
      checked={checked}
      onChange={(event) => onCheckedChange(event.target.checked)}
      {...rest}
    />
  ),
}));

vi.mock("@/components/ui/select", () => ({
  Select: ({
    value,
    onValueChange,
    children,
    ...rest
  }: { value?: string; onValueChange: (value: string) => void; children?: ReactNode } & Record<string, unknown>) => (
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

function profile(overrides: Partial<SlicerProfile> = {}): SlicerProfile {
  return {
    id: 1,
    name: "A1 mini 0.4",
    kind: "printer",
    printer_model: "A1 mini",
    nozzle_mm: 0.4,
    source: "import",
    created_by_user_id: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    data: { curr_bed_type: ["Cool Plate", "Textured PEI Plate"] },
    ...overrides,
  };
}

function recipe(overrides: Partial<PrintRecipe> = {}): PrintRecipe {
  return {
    id: 1,
    name: "Standard PLA",
    printer_profile_id: 1,
    process_profile_id: 2,
    filament_profile_ids: [3, null, null, null],
    bed_type: "Textured PEI Plate",
    options: { bed_levelling: true, flow_cali: true, timelapse: false },
    target_printer_id: null,
    target_printer_model: null,
    fanout: "single",
    n: null,
    created_by_user_id: 1,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function renderPage(initial = "/") {
  const rootRoute = createRootRoute();
  const route = createRoute({ getParentRoute: () => rootRoute, path: "/", component: ProfilesPage });
  const router = createRouter({
    routeTree: rootRoute.addChildren([route]),
    history: createMemoryHistory({ initialEntries: [initial] }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  postFormMock.mockReset();
  postMock.mockResolvedValue({ created: 0, updated: 0 });
  postFormMock.mockResolvedValue({ created: 1, updated: 0 });
  getMock.mockImplementation((path: string) => {
    if (path.startsWith("/profiles?")) {
      const kind = new URL(path, "http://x").searchParams.get("kind");
      const all = [
        profile({ id: 1, kind: "printer", name: "A1 mini 0.4" }),
        profile({ id: 2, kind: "process", name: "0.2mm Standard", printer_model: "A1 mini", data: {} }),
        profile({ id: 3, kind: "filament", name: "Bambu PLA Basic", printer_model: "A1 mini", data: {} }),
      ];
      const items = kind ? all.filter((p) => p.kind === kind) : all;
      return Promise.resolve({ items, total: items.length, nozzle_source: null });
    }
    if (path.startsWith("/recipes")) return Promise.resolve([recipe()]);
    if (path === "/printers") return Promise.resolve([]);
    if (path === "/slicer/health") return Promise.resolve({ configured: false, ok: false });
    return Promise.resolve([]);
  });
  authMock.mockReturnValue({ can: () => true, canPrinter: () => true });
});

describe("ProfilesPage", () => {
  it("shows the profiles tab by default and switches to recipes", async () => {
    renderPage();
    expect(await screen.findByText("A1 mini 0.4")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Recipes" }));
    expect(await screen.findByText("Standard PLA")).toBeInTheDocument();
  });

  it("filters profiles by kind", async () => {
    renderPage();
    await screen.findByText("A1 mini 0.4");
    fireEvent.click(screen.getByRole("button", { name: "Process" }));
    await waitFor(() =>
      expect(getMock).toHaveBeenCalledWith(expect.stringContaining("kind=process")),
    );
  });

  it("shows a 'not configured' sidecar chip when no sidecar is set up", async () => {
    renderPage();
    expect(await screen.findByText("Sidecar not configured")).toBeInTheDocument();
  });

  it("syncs Bambu system profiles from the sidecar (own printers by default, all on request)", async () => {
    getMock.mockImplementation((path: string) => {
      if (path === "/slicer/health") return Promise.resolve({ configured: true, ok: true, version: "v1" });
      if (path.startsWith("/profiles?")) return Promise.resolve({ items: [], total: 0, nozzle_source: null });
      return Promise.resolve([]);
    });
    postMock.mockResolvedValue({ created: 5, updated: 1, skipped: 2 });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Sync Bambu system profiles" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/profiles/import", { from_sidecar: true, all: false }),
    );
    expect(await screen.findByText(/5 new, 1 updated, 2 kept/)).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText(/All Bambu printers/));
    fireEvent.click(screen.getByRole("button", { name: "Sync Bambu system profiles" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenLastCalledWith("/profiles/import", { from_sidecar: true, all: true }),
    );
  });

  it("hides the sync action when no sidecar is configured", async () => {
    renderPage();
    await screen.findByText("A1 mini 0.4");
    expect(screen.queryByRole("button", { name: "Sync Bambu system profiles" })).toBeNull();
  });

  it("hides Import and New recipe for a viewer", async () => {
    authMock.mockReturnValue({ can: () => false, canPrinter: () => true });
    renderPage();
    await screen.findByText("A1 mini 0.4");
    expect(screen.queryByRole("button", { name: "Import" })).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "Recipes" }));
    await screen.findByText("Standard PLA");
    expect(screen.queryByRole("button", { name: "New recipe" })).toBeNull();
  });

  it("recipe form requires N for fanout first_n", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("tab", { name: "Recipes" }));
    fireEvent.click(await screen.findByRole("button", { name: "New recipe" }));

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "My recipe" } });
    fireEvent.change(screen.getByLabelText("Printer profile"), { target: { value: "1" } });
    fireEvent.change(await screen.findByLabelText("Process profile"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Fanout"), { target: { value: "first_n" } });

    fireEvent.click(screen.getByRole("button", { name: "Create recipe" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/N is required/);
    expect(postMock).not.toHaveBeenCalledWith("/recipes", expect.anything());
  });

  it("imports a bundle as multipart form data", async () => {
    renderPage();
    await screen.findByText("A1 mini 0.4");
    fireEvent.click(screen.getByRole("button", { name: "Import" }));

    const file = new File(["zip-bytes"], "bundle.zip", { type: "application/zip" });
    const input = screen.getByLabelText("Bundle (.zip)") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload bundle" }));

    await waitFor(() => expect(postFormMock).toHaveBeenCalled());
    const [path, formData] = postFormMock.mock.calls[0];
    expect(path).toBe("/profiles/import");
    expect((formData as FormData).get("bundle")).toBe(file);
  });
});

describe("ProfilesPage scope bar", () => {
  const printer = { id: 7, name: "Mini", model: "A1 mini", nozzle_mm: 0.4 };

  function mockScoped() {
    getMock.mockImplementation((path: string) => {
      if (path.startsWith("/profiles?")) {
        const params = new URL(path, "http://x").searchParams;
        const items = [
          profile({ id: 1, kind: "printer", name: "A1 mini 0.4", in_scope: true }),
          profile({ id: 2, kind: "process", name: "Universal draft", printer_model: null, compat: null, layer_height_mm: 0.2, in_scope: true }),
          ...(params.get("show_all") === "1"
            ? [profile({ id: 4, kind: "printer", name: "P1S 0.4", printer_model: "P1S", in_scope: false, scope_reason: "for P1S" })]
            : []),
        ];
        return Promise.resolve({ items, total: 3, nozzle_source: "live", nozzle_mm: 0.4 });
      }
      if (path.startsWith("/recipes")) return Promise.resolve([recipe()]);
      if (path === "/printers") return Promise.resolve([printer]);
      if (path === "/slicer/health") return Promise.resolve({ configured: false, ok: false });
      return Promise.resolve([]);
    });
  }

  it("defaults to the first printer in the URL and scopes the query by it", async () => {
    mockScoped();
    const router = renderPage();
    await waitFor(() => expect(router.state.location.search).toMatchObject({ printer: 7 }));
    await waitFor(() => expect(getMock).toHaveBeenCalledWith(expect.stringContaining("printer_id=7")));
    expect((await screen.findAllByTestId("nozzle-source"))[0]).toHaveTextContent("live");
    await waitFor(() => expect(screen.getByTestId("profiles-count")).toHaveTextContent("Showing 2 of 3"));
    expect(await screen.findAllByText("Any printer")).not.toHaveLength(0);
  });

  it("derives printer model, nozzle and layer height from compat", async () => {
    getMock.mockImplementation((path: string) => {
      if (path.startsWith("/profiles?")) {
        const items = [
          profile({ id: 1, kind: "process", name: "Derived", printer_model: null, nozzle_mm: null, compat: [{ model_key: "a1mini", nozzle_mm: 0.6 }, { model_key: "a1mini", nozzle_mm: 0.8 }, { model_key: "x1carbon", nozzle_mm: 0.6 }], layer_height_mm: 0.28, in_scope: true }),
          profile({ id: 2, kind: "process", name: "AnyNozzle", printer_model: null, nozzle_mm: null, compat: [{ model_key: "zz9", nozzle_mm: null }], layer_height_mm: null, in_scope: true }),
        ];
        return Promise.resolve({ items, total: 2, nozzle_source: null });
      }
      if (path === "/printers") return Promise.resolve([]);
      if (path === "/slicer/health") return Promise.resolve({ configured: false, ok: false });
      return Promise.resolve([]);
    });
    renderPage("/");
    fireEvent.click(await screen.findByRole("button", { name: "Process" }));
    expect(await screen.findByText("A1 mini, X1 Carbon")).toBeInTheDocument();
    expect(screen.getByText("0.6/0.8 mm")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Layer height" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "0.28 mm" })).toBeInTheDocument();
    expect(screen.getByText("ZZ9")).toBeInTheDocument();
    expect(screen.getByText("any")).toBeInTheDocument();
  });

  it("sends show_all and renders out-of-scope rows dimmed with a reason", async () => {
    mockScoped();
    renderPage("/?printer=7&all=1");
    const reason = await screen.findByText("for P1S");
    expect(reason.closest("tr")).toHaveClass("opacity-50");
    expect(getMock).toHaveBeenCalledWith(expect.stringContaining("show_all=1"));
    // "Showing" counts only in-scope rows
    expect(screen.getByTestId("profiles-count")).toHaveTextContent("Showing 2 of 3");
  });

  it("replaces a stale ?printer= with the first printer", async () => {
    mockScoped();
    const router = renderPage("/?printer=99");
    await waitFor(() => expect(router.state.location.search).toMatchObject({ printer: 7 }));
  });

  it("Auto nozzle clears the override", async () => {
    mockScoped();
    const router = renderPage("/?printer=7&nozzle=0.6");
    fireEvent.change(await screen.findByLabelText("Nozzle"), { target: { value: "auto" } });
    await waitFor(() => expect((router.state.location.search as { nozzle?: number }).nozzle).toBeUndefined());
  });

  it("passes compatible_only on the recipes tab unless incompatible are shown", async () => {
    mockScoped();
    renderPage("/?printer=7&tab=recipes");
    await screen.findByText("Standard PLA");
    expect(getMock).toHaveBeenCalledWith(expect.stringMatching(/^\/recipes\?.*compatible_only=1/));
    expect(getMock).toHaveBeenCalledWith(expect.stringContaining("printer_id=7"));
  });
});
