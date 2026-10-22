import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import type { StorageBackendOut } from "@/api/types";
import { SettingsPage } from "@/pages/SettingsPage";

// `vi.mock` factories are hoisted above the module's own top-level bindings
// (same pattern as LibraryPage.test.tsx/UploadPage.test.tsx), so the fakes
// have to be created through `vi.hoisted`.
const { getMock, postMock, putMock, deleteMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  putMock: vi.fn(),
  deleteMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock, post: postMock, put: putMock, delete: deleteMock },
  };
});

// Auth is mutable per test; default is an admin with a local password.
const { authState } = vi.hoisted(() => ({
  authState: { perms: ["admin"] as string[], has_password: true },
}));
vi.mock("@/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/auth")>()),
  useAuth: () => ({
    data: { id: 1, username: "u", role: "member", permissions: authState.perms, has_password: authState.has_password },
    can: (p: string) => authState.perms.includes(p),
  }),
}));

// Radix's Select never reaches an interactive open state under jsdom (same
// floating-ui/dismissable-layer limitation noted elsewhere) -- swap it for a
// plain native <select> so the Add/Edit backend dialog's backend-type picker
// is drivable via change events.
vi.mock("@/components/ui/select", () => ({
  Select: ({
    value,
    onValueChange,
    disabled,
    children,
    ...rest
  }: {
    value?: string;
    onValueChange: (value: string) => void;
    disabled?: boolean;
    children?: ReactNode;
  } & Record<string, unknown>) => (
    <select value={value} disabled={disabled} onChange={(event) => onValueChange(event.target.value)} {...rest}>
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

// Radix Tabs switches on focus/pointer, not a bare `fireEvent.click`, so tab
// activation is unreliable under jsdom. Swap it for a stateful mock that
// renders only the active panel and switches on a trigger click.
vi.mock("@/components/ui/tabs", async () => {
  const React = await vi.importActual<typeof import("react")>("react");
  const TabsCtx = React.createContext<{ value: string; setValue: (value: string) => void }>({
    value: "",
    setValue: () => {},
  });
  return {
    Tabs: ({ defaultValue, children }: { defaultValue?: string; children?: ReactNode }) => {
      const [value, setValue] = React.useState(defaultValue ?? "");
      return <TabsCtx.Provider value={{ value, setValue }}>{children}</TabsCtx.Provider>;
    },
    TabsList: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
    TabsTrigger: ({ value, children }: { value: string; children?: ReactNode }) => {
      const ctx = React.useContext(TabsCtx);
      return (
        <button role="tab" type="button" onClick={() => ctx.setValue(value)}>
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

function fakeBackend(overrides: Partial<StorageBackendOut> = {}): StorageBackendOut {
  return {
    id: 1,
    name: "Default",
    scheme: "local",
    is_default: true,
    config: { backend: "local", root: "" },
    created_at: "2026-07-05T00:00:00Z",
    ...overrides,
  };
}

// General is now the default tab (Round 10 T5), so every render mounts
// AutomationCard (`GET /settings/app` + `GET /features`) even in tests that
// only care about another tab -- these two need a well-shaped default so
// that mount doesn't error or hang on an unresolved fetch.
function fakeAppSettings() {
  return {
    printer_enabled: false,
    scan_interval_s: 3600,
    collection_sync_interval_s: 3600,
    watch_interval_s: 0,
    watch_stable_s: 5,
    filament_cost_per_kg: 20,
    machine_cost_per_hour: 0,
  };
}

function mockGet(backends: StorageBackendOut[] = []) {
  getMock.mockImplementation((path: string) => {
    if (path === "/settings/storage/backends") return Promise.resolve(backends);
    // BambuAccountCard's status query -- a well-shaped not-connected response.
    if (path === "/settings/bambu") return Promise.resolve({ connected: false, account: null, region: "global" });
    if (path === "/settings/app") return Promise.resolve(fakeAppSettings());
    if (path === "/features") return Promise.resolve({ printer_enabled: false, watch_dir: null, watch_enabled: false });
    return Promise.resolve([]);
  });
}

// SettingsPage renders inside the router in the real app (its Accounts tab links
// to /collections), so the harness needs a router context -- a bare render makes
// TanStack's `useLinkProps` throw. Same memory-router setup as SavedPanel.test.
function renderSettingsPage() {
  const rootRoute = createRootRoute();
  const home = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: SettingsPage,
  });
  const collections = createRoute({
    getParentRoute: () => rootRoute,
    path: "/collections",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([home, collections]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  putMock.mockReset();
  deleteMock.mockReset();
  authState.perms = ["admin", "settings:write"];
  authState.has_password = true;
});

describe("SettingsPage access gating", () => {
  it("hides the Access tab for a member without admin", async () => {
    mockGet();
    authState.perms = ["settings:write"];
    renderSettingsPage();
    await screen.findAllByRole("tab");
    expect(screen.queryByRole("tab", { name: "Access" })).not.toBeInTheDocument();
  });

  it("shows the Access tab when can(admin)", async () => {
    mockGet();
    renderSettingsPage();
    fireEvent.click(await screen.findByRole("tab", { name: "Access" }));
    expect(await screen.findByText("SSO providers")).toBeInTheDocument();
  });

  it("hides Storage and Printer without settings:write, and Password without a local password", async () => {
    mockGet();
    authState.perms = [];
    authState.has_password = false;
    renderSettingsPage();
    await screen.findByText("Automation & scheduling");
    expect(screen.queryByRole("tab", { name: "Storage" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Printer" })).not.toBeInTheDocument();
    expect(screen.queryByText("Password")).not.toBeInTheDocument();
  });
});

describe("SettingsPage tabs", () => {
  it("shows exactly General, Storage, Printer, Accounts, and Access tabs (admin) -- no Scan tab", async () => {
    mockGet();

    renderSettingsPage();

    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual(["General", "Storage", "Printer", "Accounts", "Access"]);
    expect(screen.queryByRole("tab", { name: "Scan" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Imports" })).not.toBeInTheDocument();
  });

  // Round 10 T5: General is the new leftmost tab and the default -- the
  // automation/scheduling and password cards should be visible with no click.
  it("renders General as the default tab, with the automation and password cards", async () => {
    mockGet();

    renderSettingsPage();

    expect(await screen.findByText("Automation & scheduling")).toBeInTheDocument();
    expect(screen.getByText("Password")).toBeInTheDocument();
  });

  // Round 10 T5: the printer on/off toggle lives ABOVE the printer setup
  // form and is always visible, regardless of the flag's current value.
  it("renders the printer-enabled toggle above printer setup in the Printer tab", async () => {
    mockGet();

    renderSettingsPage();

    fireEvent.click(await screen.findByRole("tab", { name: "Printer" }));

    expect(await screen.findByRole("switch", { name: "Enable printer integration" })).toBeInTheDocument();
    expect(screen.getByText("Printer setup")).toBeInTheDocument();
  });

  it("renders the Bambu account card in the Accounts tab", async () => {
    mockGet();

    renderSettingsPage();

    fireEvent.click(await screen.findByRole("tab", { name: "Accounts" }));

    expect(await screen.findByText("Bambu Lab account")).toBeInTheDocument();
    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
  });

  // Round 8 T6: SlicerIntegrationCard joins BrowserExtensionCard as a
  // single-column card in the Accounts grid (see the packing comment in
  // SettingsPage.tsx).
  it("renders the slicer integration card in the Accounts tab", async () => {
    mockGet();

    renderSettingsPage();

    fireEvent.click(await screen.findByRole("tab", { name: "Accounts" }));

    expect(await screen.findByText("Slicer integration")).toBeInTheDocument();
    expect(
      screen.getByText("Send sliced files straight from Bambu Studio to your library."),
    ).toBeInTheDocument();
  });

  // You connect an account here, then go looking for its collections. They live
  // on their own page now, so the Accounts tab has to say where.
  it("points from the Accounts tab to the Collections page", async () => {
    mockGet();

    renderSettingsPage();

    fireEvent.click(await screen.findByRole("tab", { name: "Accounts" }));

    const link = await screen.findByRole("link", { name: "Collections" });
    expect(link).toHaveAttribute("href", "/collections");
  });

  // The Scan tab was folded into Storage: ScanReport now renders alongside
  // StorageBackendsCard under the Storage tab instead of its own tab.
  it("renders the scan report under the Storage tab", async () => {
    mockGet();

    renderSettingsPage();

    fireEvent.click(await screen.findByRole("tab", { name: "Storage" }));

    expect(await screen.findByText("Storage scan")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run scan" })).toBeInTheDocument();
  });
});

describe("SettingsPage -- storage backends list", () => {
  it("renders every configured backend with a Default badge on the write target", async () => {
    mockGet([
      fakeBackend({ id: 1, name: "Primary NAS", is_default: true }),
      fakeBackend({ id: 2, name: "Cold storage", scheme: "s3", is_default: false }),
    ]);

    renderSettingsPage();
    fireEvent.click(await screen.findByRole("tab", { name: "Storage" }));

    expect(await screen.findByText("Primary NAS")).toBeInTheDocument();
    expect(screen.getByText("Cold storage")).toBeInTheDocument();
    // "Default" also names the table column header -- scope to the row.
    const primaryRow = screen.getByText("Primary NAS").closest("tr");
    if (!primaryRow) throw new Error("primary row not found");
    expect(within(primaryRow).getByText("Default")).toBeInTheDocument();
  });

  it("adds a new storage backend through the Add backend dialog", async () => {
    mockGet([fakeBackend({ id: 1, name: "Primary", is_default: true })]);
    postMock.mockImplementation((path: string) => {
      if (path === "/settings/storage/backends") {
        return Promise.resolve(fakeBackend({ id: 2, name: "Backup", is_default: false }));
      }
      return Promise.resolve({});
    });

    renderSettingsPage();
    fireEvent.click(await screen.findByRole("tab", { name: "Storage" }));
    await screen.findByText("Primary");

    fireEvent.click(screen.getByRole("button", { name: "Add backend" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Backup" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add backend" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/settings/storage/backends", {
        name: "Backup",
        config: { backend: "local" },
      }),
    );
  });

  it("sets a non-default backend as the new default", async () => {
    mockGet([
      fakeBackend({ id: 1, name: "Primary", is_default: true }),
      fakeBackend({ id: 2, name: "Backup", is_default: false }),
    ]);
    postMock.mockResolvedValue(fakeBackend({ id: 2, name: "Backup", is_default: true }));

    renderSettingsPage();
    fireEvent.click(await screen.findByRole("tab", { name: "Storage" }));
    await screen.findByText("Backup");

    const backupRow = screen.getByText("Backup").closest("tr");
    if (!backupRow) throw new Error("backup row not found");
    fireEvent.click(within(backupRow).getByRole("button", { name: "Set default" }));

    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/settings/storage/backends/2/default"));
  });

  it("moves the whole library to a non-default backend via 'Move all here'", async () => {
    mockGet([
      fakeBackend({ id: 1, name: "Primary", is_default: true }),
      fakeBackend({ id: 2, name: "Backup", is_default: false }),
    ]);
    postMock.mockResolvedValue({ id: "job-1", type: "relocate_all", state: "queued" });

    renderSettingsPage();
    fireEvent.click(await screen.findByRole("tab", { name: "Storage" }));
    await screen.findByText("Backup");

    const backupRow = screen.getByText("Backup").closest("tr");
    if (!backupRow) throw new Error("backup row not found");
    fireEvent.click(within(backupRow).getByRole("button", { name: "Move all here" }));

    // The row trigger and the confirm dialog's action share the label
    // "Move all here" -- scope the confirm to the (portal) dialog.
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Move all here" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/settings/storage/backends/2/migrate"),
    );
  });

  it("surfaces the backend's 409 guardrail error when a delete is rejected", async () => {
    mockGet([
      fakeBackend({ id: 1, name: "Primary", is_default: true }),
      fakeBackend({ id: 2, name: "Backup", is_default: false }),
    ]);
    deleteMock.mockRejectedValue(
      new ApiError(409, "storage backend 2 still holds 3 file(s); relocate them first"),
    );

    renderSettingsPage();
    fireEvent.click(await screen.findByRole("tab", { name: "Storage" }));
    await screen.findByText("Backup");

    const backupRow = screen.getByText("Backup").closest("tr");
    if (!backupRow) throw new Error("backup row not found");
    fireEvent.click(within(backupRow).getByRole("button", { name: "Delete" }));

    const confirmDialog = await screen.findByRole("dialog");
    fireEvent.click(within(confirmDialog).getByRole("button", { name: "Delete" }));

    expect(await screen.findByText(/still holds 3 file\(s\); relocate them first/)).toBeInTheDocument();
  });
});
