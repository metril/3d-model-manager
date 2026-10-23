import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ProfilesPage />
    </QueryClientProvider>,
  );
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
      return Promise.resolve(kind ? all.filter((p) => p.kind === kind) : all);
    }
    if (path === "/recipes") return Promise.resolve([recipe()]);
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
      if (path.startsWith("/profiles?")) return Promise.resolve([]);
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
    fireEvent.click(screen.getByRole("tab", { name: "Recipes" }));
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
