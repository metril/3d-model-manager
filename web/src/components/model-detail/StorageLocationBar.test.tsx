import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { ApiError } from "@/api/client";
import type { JobOut, ModelDetail, StorageBackendOut } from "@/api/types";
import { StorageLocationBar } from "@/components/model-detail/StorageLocationBar";

// `vi.mock` factories are hoisted above the module's own top-level bindings
// (same pattern as SettingsPage.test.tsx), so the fakes have to be created
// through `vi.hoisted`.
const { getMock, postMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock, post: postMock },
  };
});

// Radix's Select never reaches an interactive open state under jsdom (same
// floating-ui/dismissable-layer limitation SettingsPage.test.tsx/
// JobsPage.test.tsx already work around) -- swap it for a plain native
// <select>. Unlike those pages (one Select each), this dialog shows TWO
// Selects at once (target backend + mode), so -- unlike those files' mocks
// -- this one forwards `...rest`, which carries the `aria-label`
// `StorageLocationBar` puts directly on the `Select` root (see that file's
// comment) onto the native element, letting `getByRole("combobox", {
// name })` tell the two apart.
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

function fakeModel(overrides: Partial<ModelDetail> = {}): ModelDetail {
  return {
    id: 1,
    slug: "test-model",
    name: "Test Model",
    description: null,
    source_url: null,
    source_site: null,
    source_author: null,
    source_license: null,
    source_collection_id: null,
    source_collection_title: null,
    imported_at: null,
    cover_blob_hash: null,
    is_archived: false,
    created_at: "2026-06-01T12:00:00Z",
    updated_at: "2026-06-01T12:00:00Z",
    tags: [],
    notes: [],
    current_revision: null,
    backends: [{ id: 1, name: "Default" }],
    favorite: false,
    print_count: 0,
    last_printed_at: null,
    metadata: null,
    print_tips: null,
    ...overrides,
  };
}

function fakeBackend(overrides: Partial<StorageBackendOut> = {}): StorageBackendOut {
  return {
    id: 1,
    name: "Default",
    scheme: "local",
    is_default: true,
    config: { backend: "local", root: "" },
    created_at: "2026-06-01T12:00:00Z",
    ...overrides,
  };
}

function fakeJob(overrides: Partial<JobOut> = {}): JobOut {
  return {
    id: "job-1",
    celery_id: "job-1",
    type: "relocate_model_storage",
    subject_type: "model",
    subject_id: 1,
    state: "queued",
    attempts: 0,
    max_attempts: 3,
    error: null,
    created_at: "2026-06-01T12:00:00Z",
    updated_at: "2026-06-01T12:00:00Z",
    ...overrides,
  };
}

function mockGet(backends: StorageBackendOut[], jobs: JobOut[] = []) {
  getMock.mockImplementation((path: string) => {
    if (path === "/settings/storage/backends") return Promise.resolve(backends);
    if (path.startsWith("/jobs")) return Promise.resolve(jobs); // backend API path, unrelated to the /system/jobs frontend route
    return Promise.resolve([]);
  });
}

// `StorageLocationBar` no longer owns a trigger button of its own -- the
// "Move / Copy to backend…" item that opens it now lives in `ModelHeader`'s
// overflow menu, and this dialog's `open` state is passed in as a
// controlled pair. This harness stands in for that parent, defaulting the
// dialog open (as if the menu item had just been selected) so these tests
// can drive the dialog's own fields directly.
function ControlledBar({ model, initialOpen = true }: { model: ModelDetail; initialOpen?: boolean }) {
  const [open, setOpen] = useState(initialOpen);
  return <StorageLocationBar model={model} open={open} onOpenChange={setOpen} />;
}

function renderBar(model: ModelDetail, { initialOpen = true }: { initialOpen?: boolean } = {}) {
  const rootRoute = createRootRoute();
  const modelRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/models/$slug",
    component: () => <ControlledBar model={model} initialOpen={initialOpen} />,
  });
  const jobsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/system/jobs", component: () => null });
  const router = createRouter({
    routeTree: rootRoute.addChildren([modelRoute, jobsRoute]),
    history: createMemoryHistory({ initialEntries: [`/models/${model.slug}`] }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("StorageLocationBar", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
  });

  it("shows a badge for every backend currently holding the model's files", async () => {
    mockGet([fakeBackend()]);
    const model = fakeModel({
      backends: [
        { id: 1, name: "Default" },
        { id: 2, name: "NAS" },
      ],
    });

    renderBar(model, { initialOpen: false });

    expect(await screen.findByText("Default")).toBeInTheDocument();
    expect(screen.getByText("NAS")).toBeInTheDocument();
  });

  it("shows a placeholder when the model has no known backend", async () => {
    mockGet([]);
    renderBar(fakeModel({ backends: [] }), { initialOpen: false });

    expect(await screen.findByText("unknown")).toBeInTheDocument();
  });

  it("excludes the model's current backend(s) from the relocate target choices", async () => {
    mockGet([fakeBackend({ id: 1, name: "Default" }), fakeBackend({ id: 2, name: "NAS", is_default: false })]);
    const model = fakeModel({ backends: [{ id: 1, name: "Default" }] });

    renderBar(model);

    const dialog = await screen.findByRole("dialog");
    const targetSelect = within(dialog).getByRole("combobox", { name: "Target backend" });
    expect(within(targetSelect).queryByText("Default")).not.toBeInTheDocument();
    expect(await within(targetSelect).findByText("NAS")).toBeInTheDocument();
  });

  it("disables Start until a target backend is chosen", async () => {
    mockGet([fakeBackend({ id: 1 }), fakeBackend({ id: 2, name: "NAS", is_default: false })]);

    renderBar(fakeModel({ backends: [{ id: 1, name: "Default" }] }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Start" })).toBeDisabled();
  });

  it("dispatches relocate with the picked target backend and mode, then surfaces the started job", async () => {
    mockGet(
      [fakeBackend({ id: 1 }), fakeBackend({ id: 2, name: "NAS", is_default: false })],
      [fakeJob({ state: "running" })],
    );
    postMock.mockResolvedValue(fakeJob({ state: "queued" }));

    renderBar(fakeModel({ backends: [{ id: 1, name: "Default" }] }));

    const dialog = await screen.findByRole("dialog");
    const targetSelect = within(dialog).getByRole("combobox", { name: "Target backend" });
    await within(targetSelect).findByText("NAS");
    fireEvent.change(targetSelect, { target: { value: "2" } });
    fireEvent.change(within(dialog).getByRole("combobox", { name: "Action" }), {
      target: { value: "replicate" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Start" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/models/test-model/relocate", {
        target_backend_id: 2,
        mode: "replicate",
      }),
    );

    expect(await screen.findByText(/Relocation running/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View jobs" })).toHaveAttribute("href", "/system/jobs");
  });

  it("surfaces a relocate error instead of silently failing", async () => {
    mockGet([fakeBackend({ id: 1 }), fakeBackend({ id: 2, name: "NAS", is_default: false })]);
    postMock.mockRejectedValue(new ApiError(409, "target backend is busy"));

    renderBar(fakeModel({ backends: [{ id: 1, name: "Default" }] }));

    const dialog = await screen.findByRole("dialog");
    const targetSelect = within(dialog).getByRole("combobox", { name: "Target backend" });
    await within(targetSelect).findByText("NAS");
    fireEvent.change(targetSelect, { target: { value: "2" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Start" }));

    await waitFor(() => expect(postMock).toHaveBeenCalled());
    expect(await screen.findByRole("alert")).toHaveTextContent("busy");
  });
});
