import { MutationCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import { LibraryPage } from "@/pages/LibraryPage";
import type { CategoryOut, FollowedCollection, ModelSummary } from "@/api/types";

const authState = vi.hoisted(() => ({ canAll: true }));
vi.mock("@/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/auth")>()),
  useAuth: () => ({
    data: { id: 1, username: "tester" },
    can: () => authState.canAll,
    canPrinter: () => true,
  }),
}));


// `vi.mock` factories are hoisted above the module's own top-level
// bindings, so the mock function has to be created through `vi.hoisted`.
const { getMock, postMock, toastSuccessMock, toastErrorMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn().mockResolvedValue({ updated: 0 }),
  toastSuccessMock: vi.fn(),
  toastErrorMock: vi.fn(),
}));

// Fakes a rejecting queryFn by mocking the fetch wrapper the gallery query
// runs through (`useModelsQuery` -> `api.get`), so the real react-query
// pipeline (isError/error/refetch) is exercised end to end rather than
// stubbing the hook's return value. `useEnqueueModel` (bulk "Add to queue")
// also goes through this same mocked `api.post`, so per-test overrides of
// `postMock` can simulate a queue failure without mocking `@/api/queue`.
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock, post: postMock },
  };
});

// Fix-review F3's "toast an error on total add-to-queue failure" test needs
// to observe the toast calls `SelectionActionBar.addToQueue` makes.
vi.mock("sonner", () => ({
  toast: { success: toastSuccessMock, error: toastErrorMock },
}));

// jsdom doesn't implement `ResizeObserver` (the virtualized grid's column
// count comes from observing the grid container's width, R9-A item 2). This
// fake is controllable: it records every observed element and its
// constructor's callback, so a test can fire a synthetic resize (`fireGridResize`)
// and assert the resulting column count / painted `gridTemplateColumns` --
// covering fix wave finding 6, which the old no-op stub could never reach
// (it left the grid stuck at its 2-column fallback, which happened to match
// the pre-fix bug).
let resizeObserverCallbacks: ResizeObserverCallback[] = [];
let resizeObserverTargets: Element[] = [];
class FakeResizeObserver implements ResizeObserver {
  constructor(callback: ResizeObserverCallback) {
    resizeObserverCallbacks.push(callback);
  }
  observe(target: Element) {
    resizeObserverTargets.push(target);
  }
  unobserve(target: Element) {
    resizeObserverTargets = resizeObserverTargets.filter((el) => el !== target);
  }
  disconnect() {
    resizeObserverTargets = [];
  }
}
vi.stubGlobal("ResizeObserver", FakeResizeObserver);

/** Fires every currently-registered ResizeObserver callback with `width` as
 * the observed content-box width, as if the grid container had been
 * resized to that width in a real browser. */
function fireGridResize(width: number) {
  const entries = resizeObserverTargets.map(
    (target) => ({ target, contentRect: { width } }) as ResizeObserverEntry,
  );
  resizeObserverCallbacks.forEach((callback) => callback(entries, {} as ResizeObserver));
}

// Radix's Popover never reaches an interactive open state under jsdom (same
// floating-ui/dismissable-layer limitation documented for `<Select>` in
// ViewerTab.test.tsx) -- render trigger/content unconditionally in place so
// the Collection (and Tags) facet's options are reachable without needing a
// real open click.
vi.mock("@/components/ui/popover", () => ({
  Popover: ({ children }: { children?: ReactNode }) => <>{children}</>,
  PopoverTrigger: ({ children }: { children?: ReactNode }) => <>{children}</>,
  PopoverContent: ({ children }: { children?: ReactNode }) => <>{children}</>,
}));

// Radix's Select never reaches an interactive open state under jsdom (same
// limitation documented in SettingsPage.test.tsx) -- swap it for a plain
// native <select> so the sort dropdown's options are inspectable/drivable.
vi.mock("@/components/ui/select", () => ({
  Select: ({
    value,
    onValueChange,
    children,
  }: {
    value?: string;
    onValueChange: (value: string) => void;
    children?: ReactNode;
  }) => (
    <select aria-label="Sort by" value={value} onChange={(event) => onValueChange(event.target.value)}>
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

function renderLibraryPage(initialEntries: string[] = ["/library"]) {
  const rootRoute = createRootRoute();
  const libraryRoute = createRoute({ getParentRoute: () => rootRoute, path: "/library", component: LibraryPage });
  const uploadRoute = createRoute({ getParentRoute: () => rootRoute, path: "/upload", component: () => null });
  // A modified click on a card must NOT navigate here -- so a matching
  // detail route exists to prove it (same shape as ModelCard.test.tsx).
  const detailRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/models/$slug",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([libraryRoute, uploadRoute, detailRoute]),
    history: createMemoryHistory({ initialEntries }),
  });
  // Mirrors the app's real global MutationCache error toast
  // (web/src/queryClient.ts) -- the bulk mutations deliberately have NO
  // local onError (Round 11 fix wave: a local toast would stack a second,
  // identical toast on top of this one in production), so error-path tests
  // must exercise the global handler to assert what users actually see.
  const queryClient = new QueryClient({
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        if (mutation.meta?.silentError) return;
        toastErrorMock(error instanceof ApiError ? error.detail : "Something went wrong");
      },
    }),
    defaultOptions: { queries: { retry: false } },
  });
  return {
    router,
    ...render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    ),
  };
}

function mockGalleryOk() {
  getMock.mockImplementation((path: string) => {
    if (path.startsWith("/models")) return Promise.resolve({ items: [], next_cursor: null });
    return Promise.resolve([]);
  });
}

const FOLLOWED_COLLECTION: FollowedCollection = {
  id: 5,
  site: "thingiverse",
  list_id: "list-1",
  kind: "collection",
  title: "Cool Prints",
  mode: "auto",
  last_synced_at: null,
  last_error: null,
  created_at: "2026-01-01T00:00:00Z",
  preview_thumbnails: [],
};

function mockGalleryOkWithCollections(collections: FollowedCollection[]) {
  getMock.mockImplementation((path: string) => {
    if (path.startsWith("/models")) return Promise.resolve({ items: [], next_cursor: null });
    if (path.startsWith("/collections")) return Promise.resolve(collections);
    return Promise.resolve([]);
  });
}

const GALLERY_MODEL: ModelSummary = {
  id: 1,
  slug: "test-model",
  name: "Test Model",
  description: null,
  tags: ["fantasy"],
  updated_at: "2026-06-01T12:00:00Z",
  created_at: "2026-06-01T12:00:00Z",
  file_count: 1,
  formats: ["stl"],
  cover: null,
  render_url: null,
  print_time_s: null,
  has_sliced: false,
  source_site: null,
  source_collection_id: null,
  source_collection_title: null,
  favorite: false,
  dims_mm: null,
  best_slicer_file: null,
  printable_file: null,
};

const GALLERY_MODEL_2: ModelSummary = {
  ...GALLERY_MODEL,
  id: 2,
  slug: "test-model-2",
  name: "Test Model 2",
};

const GALLERY_MODEL_3: ModelSummary = {
  ...GALLERY_MODEL,
  id: 3,
  slug: "test-model-3",
  name: "Test Model 3",
};

const GALLERY_MODEL_4: ModelSummary = {
  ...GALLERY_MODEL,
  id: 4,
  slug: "test-model-4",
  name: "Test Model 4",
};

function mockGalleryOkWithModels(models: ModelSummary[]) {
  getMock.mockImplementation((path: string) => {
    if (path.startsWith("/models")) return Promise.resolve({ items: models, next_cursor: null });
    if (path.startsWith("/tags")) return Promise.resolve([{ id: 1, name: "fantasy", color: "#14b8a6" }]);
    return Promise.resolve([]);
  });
}

const CATEGORY: CategoryOut = { id: 3, name: "Miniatures", color: "red", model_count: 2 };

function mockGalleryOkWithCategories(categories: CategoryOut[]) {
  getMock.mockImplementation((path: string) => {
    if (path.startsWith("/models")) return Promise.resolve({ items: [], next_cursor: null });
    if (path.startsWith("/categories")) return Promise.resolve(categories);
    return Promise.resolve([]);
  });
}

function lastModelsCall(): string {
  const calls = getMock.mock.calls.filter((call: unknown[]) => (call[0] as string).startsWith("/models"));
  const last = calls.at(-1);
  if (!last) throw new Error("no /models call recorded");
  return last[0] as string;
}

function lastBulkCall(): { path: string; body: unknown } {
  const calls = postMock.mock.calls.filter((call: unknown[]) => (call[0] as string) === "/models/bulk");
  const last = calls.at(-1);
  if (!last) throw new Error("no /models/bulk call recorded");
  return { path: last[0] as string, body: last[1] };
}

function lastBulkDeleteCall(): { path: string; body: unknown } {
  const calls = postMock.mock.calls.filter((call: unknown[]) => (call[0] as string) === "/models/bulk-delete");
  const last = calls.at(-1);
  if (!last) throw new Error("no /models/bulk-delete call recorded");
  return { path: last[0] as string, body: last[1] };
}

beforeEach(() => {
  postMock.mockReset();
  postMock.mockResolvedValue({ updated: 0 });
  toastSuccessMock.mockClear();
  toastErrorMock.mockClear();
  resizeObserverCallbacks = [];
  resizeObserverTargets = [];
  window.localStorage.clear();
});

describe("LibraryPage", () => {
  it("hides Scan library without settings:write and shows it with", async () => {
    authState.canAll = false;
    const first = renderLibraryPage();
    await screen.findByRole("button", { name: /table|grid|list/i }).catch(() => null);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByRole("button", { name: /scan library/i })).not.toBeInTheDocument();
    first.unmount();
    authState.canAll = true;
    renderLibraryPage();
    expect(await screen.findByRole("button", { name: /scan library/i })).toBeInTheDocument();
  });

  it("renders an error card with a retry button when the gallery fetch fails, not the empty state", async () => {
    getMock.mockImplementation((path: string) => {
      if (path.startsWith("/models")) return Promise.reject(new ApiError(500, "Database is unavailable"));
      return Promise.resolve([]);
    });

    renderLibraryPage();

    expect(await screen.findByText("Couldn't load models")).toBeInTheDocument();
    expect(screen.getByText("Database is unavailable")).toBeInTheDocument();
    expect(screen.queryByText("No models yet")).not.toBeInTheDocument();

    const callsBeforeRetry = getMock.mock.calls.filter((call: unknown[]) => (call[0] as string).startsWith("/models")).length;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => {
      const callsAfterRetry = getMock.mock.calls.filter((call: unknown[]) => (call[0] as string).startsWith("/models")).length;
      expect(callsAfterRetry).toBeGreaterThan(callsBeforeRetry);
    });
  });

  it("shows a filtered empty state with Clear filters when a filter is active, plain empty state otherwise", async () => {
    mockGalleryOk();
    renderLibraryPage();
    await screen.findByText("No models yet");
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Favorites" }));
    await screen.findByText("No models match these filters");
    expect(screen.queryByText("No models yet")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await screen.findByText("No models yet");
    await waitFor(() => expect(lastModelsCall()).not.toContain("favorite="));
  });

  it("filters by a single format via the chip facet, clearing back to All", async () => {
    mockGalleryOk();
    renderLibraryPage();
    await screen.findByText("No models yet");

    fireEvent.click(screen.getByRole("button", { name: "STL" }));
    await waitFor(() => expect(lastModelsCall()).toContain("format=stl"));

    fireEvent.click(screen.getByRole("button", { name: "All" }));
    await waitFor(() => expect(lastModelsCall()).not.toContain("format="));
  });

  it("Type multi-select sends a comma-joined format; chips still replace it", async () => {
    mockGalleryOk();
    renderLibraryPage();
    await screen.findByText("No models yet");

    fireEvent.click(screen.getByRole("button", { name: /^Type/ }));
    fireEvent.click(await screen.findByRole("checkbox", { name: "STL" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: "OBJ" }));
    await waitFor(() => expect(decodeURIComponent(lastModelsCall())).toContain("format=stl,obj"));

    fireEvent.click(screen.getByRole("button", { name: "3MF" }));
    await waitFor(() => expect(lastModelsCall()).toContain("format=3mf"));
    expect(lastModelsCall()).not.toContain("obj");
  });

  it("only lets one format be active at a time (single-select chips)", async () => {
    mockGalleryOk();
    renderLibraryPage();
    await screen.findByText("No models yet");

    fireEvent.click(screen.getByRole("button", { name: "STL" }));
    await waitFor(() => expect(lastModelsCall()).toContain("format=stl"));

    fireEvent.click(screen.getByRole("button", { name: "3MF" }));
    await waitFor(() => expect(lastModelsCall()).toContain("format=3mf"));
    expect(lastModelsCall()).not.toContain("format=stl");
  });

  it("adds has_sliced=true to the gallery query when 'Sliced only' is checked", async () => {
    mockGalleryOk();
    renderLibraryPage();
    await screen.findByText("No models yet");

    fireEvent.click(screen.getByRole("checkbox", { name: "Sliced only" }));
    await waitFor(() => expect(lastModelsCall()).toContain("has_sliced=true"));

    fireEvent.click(screen.getByRole("checkbox", { name: "Sliced only" }));
    await waitFor(() => expect(lastModelsCall()).not.toContain("has_sliced"));
  });

  it("renders followed collections as options in the Collection facet", async () => {
    mockGalleryOkWithCollections([FOLLOWED_COLLECTION]);
    renderLibraryPage();
    await screen.findByText("No models yet");

    expect(await screen.findByText("Cool Prints (thingiverse)")).toBeInTheDocument();
  });

  it("filters by a followed collection via the facet, and clears back on a second click", async () => {
    mockGalleryOkWithCollections([FOLLOWED_COLLECTION]);
    renderLibraryPage();
    await screen.findByText("No models yet");

    const chip = await screen.findByText("Cool Prints (thingiverse)");
    fireEvent.click(chip);
    await waitFor(() => expect(lastModelsCall()).toContain("collection=5"));
    expect(await screen.findByRole("button", { name: "Collection: Cool Prints" })).toBeInTheDocument();

    fireEvent.click(screen.getByText("Cool Prints (thingiverse)"));
    await waitFor(() => expect(lastModelsCall()).not.toContain("collection="));
  });

  it("seeds the collection filter from a ?collection= URL search param on mount", async () => {
    mockGalleryOkWithCollections([FOLLOWED_COLLECTION]);
    renderLibraryPage(["/library?collection=5"]);

    await waitFor(() => expect(lastModelsCall()).toContain("collection=5"));
    expect(await screen.findByRole("button", { name: "Collection: Cool Prints" })).toBeInTheDocument();
  });

  // Sidebar collection links only change `?collection=` on the already-mounted
  // `/library` route, so the facet must follow the URL live, not seed once.
  it("switches the collection filter live when ?collection= changes without remounting", async () => {
    mockGalleryOkWithCollections([FOLLOWED_COLLECTION, { ...FOLLOWED_COLLECTION, id: 6, title: "Other Prints" }]);
    const { router } = renderLibraryPage(["/library?collection=5"]);
    await waitFor(() => expect(lastModelsCall()).toContain("collection=5"));

    await act(async () => {
      await router.navigate({ to: "/library", search: { collection: 6 } });
    });

    await waitFor(() => expect(lastModelsCall()).toContain("collection=6"));
    expect(await screen.findByRole("button", { name: "Collection: Other Prints" })).toBeInTheDocument();
  });

  it("adds favorite=true to the gallery query when the Favorites facet is toggled on, and clears it back off", async () => {
    mockGalleryOk();
    renderLibraryPage();
    await screen.findByText("No models yet");

    fireEvent.click(screen.getByRole("button", { name: "Favorites" }));
    await waitFor(() => expect(lastModelsCall()).toContain("favorite=true"));

    fireEvent.click(screen.getByRole("button", { name: "Favorites" }));
    await waitFor(() => expect(lastModelsCall()).not.toContain("favorite="));
  });

  it("adds archived=true to the gallery query when the Include archived facet is toggled on, and clears it back off", async () => {
    mockGalleryOk();
    renderLibraryPage();
    await screen.findByText("No models yet");

    fireEvent.click(screen.getByRole("button", { name: "Include archived" }));
    await waitFor(() => expect(lastModelsCall()).toContain("archived=true"));

    fireEvent.click(screen.getByRole("button", { name: "Include archived" }));
    await waitFor(() => expect(lastModelsCall()).not.toContain("archived="));
  });

  it("checking a card's (always-mounted) checkbox reveals a floating action bar", async () => {
    mockGalleryOkWithModels([GALLERY_MODEL]);
    renderLibraryPage();
    await screen.findByText("Test Model");

    const checkbox = await screen.findByRole("checkbox", { name: "Select Test Model" });
    expect(screen.queryByText(/selected/)).not.toBeInTheDocument();

    fireEvent.click(checkbox);
    expect(await screen.findByText("1 selected")).toBeInTheDocument();
  });

  it("tag filter any/all and the file-tag filter drive the tags / tag_mode / file_tags query params", async () => {
    getMock.mockImplementation((path: string) => {
      if (path.startsWith("/models")) return Promise.resolve({ items: [], next_cursor: null });
      if (path.startsWith("/tags"))
        return Promise.resolve([
          { id: 1, name: "fantasy", color: "#14b8a6" },
          { id: 2, name: "dragon", color: null },
        ]);
      return Promise.resolve([]);
    });
    renderLibraryPage();
    const tagList = await screen.findByRole("list", { name: "Tags filter" });
    fireEvent.click(await within(tagList).findByRole("button", { name: /fantasy/ }));
    fireEvent.click(within(tagList).getByRole("button", { name: /dragon/ }));

    const modelCalls = () =>
      getMock.mock.calls.map((call: unknown[]) => String(call[0])).filter((path: string) => path.startsWith("/models?"));
    await waitFor(() => expect(modelCalls().at(-1)).toContain("tags=1&tags=2"));
    expect(modelCalls().at(-1)).not.toContain("tag_mode");

    fireEvent.click(within(screen.getByRole("group", { name: "Match mode" })).getByRole("button", { name: "All" }));
    await waitFor(() => expect(modelCalls().at(-1)).toContain("tag_mode=all"));

    const fileList = screen.getByRole("list", { name: "Has file tagged filter" });
    fireEvent.click(within(fileList).getByRole("button", { name: /dragon/ }));
    await waitFor(() => expect(modelCalls().at(-1)).toContain("file_tags=2"));
  });

  it("a second Enter while the tag mutation is in flight doesn't fire a second bulk update", async () => {
    mockGalleryOkWithModels([GALLERY_MODEL]);
    // Never settles: keeps the mutation pending so the in-flight guard is
    // what's under test, not mutation timing.
    postMock.mockImplementation(() => new Promise(() => {}));
    renderLibraryPage();
    await screen.findByText("Test Model");
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Test Model" }));
    await screen.findByText("1 selected");

    const addList = (await screen.findAllByRole("list", { name: "Tags" }))[0]!;
    const fantasy = await within(addList).findByRole("button", { name: /fantasy/ });
    fireEvent.click(fantasy);
    fireEvent.click(fantasy);

    await waitFor(() =>
      expect(postMock.mock.calls.filter((call: unknown[]) => call[0] === "/models/bulk")).toHaveLength(1),
    );
  });

  it("bulk-favoriting the selection POSTs /models/bulk with the selected ids and favorite:true", async () => {
    mockGalleryOkWithModels([GALLERY_MODEL]);
    renderLibraryPage();
    await screen.findByText("Test Model");
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Test Model" }));
    await screen.findByText("1 selected");

    fireEvent.click(screen.getByRole("button", { name: "Favorite" }));

    await waitFor(() =>
      expect(lastBulkCall()).toEqual({ path: "/models/bulk", body: { ids: [1], favorite: true } }),
    );
  });

  it("toasts an error, and no success toast, when every add-to-queue call in the selection fails", async () => {
    // Fix-review F3: `Promise.allSettled` swallows rejections silently --
    // a total failure must still tell the user something went wrong.
    mockGalleryOkWithModels([GALLERY_MODEL]);
    postMock.mockImplementation((path: string) => {
      if (path === "/queue") return Promise.reject(new ApiError(409, "already queued"));
      return Promise.resolve({ updated: 0 });
    });
    renderLibraryPage();
    await screen.findByText("Test Model");
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Test Model" }));
    await screen.findByText("1 selected");

    fireEvent.click(screen.getByRole("button", { name: "Add to queue" }));

    // Exactly ONE toast: the bulk loop marks its enqueue mutation
    // `silentError`, so the global MutationCache handler stays quiet and
    // doesn't stack a per-model toast on top of this summary.
    await waitFor(() => expect(toastErrorMock).toHaveBeenCalledExactlyOnceWith("Failed to add 1 model to queue"));
    expect(toastSuccessMock).not.toHaveBeenCalled();
  });

  it("bulk-deleting the selection POSTs /models/bulk-delete, toasts, and exits select mode", async () => {
    mockGalleryOkWithModels([GALLERY_MODEL, GALLERY_MODEL_2]);
    postMock.mockResolvedValueOnce({ deleted: 2 });
    renderLibraryPage();
    await screen.findByText("Test Model");
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Test Model" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Test Model 2" }));
    await screen.findByText("2 selected");

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Move 2 models to trash?")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Move to trash" }));

    await waitFor(() =>
      expect(lastBulkDeleteCall()).toEqual({ path: "/models/bulk-delete", body: { ids: [1, 2] } }),
    );
    await waitFor(() => expect(toastSuccessMock).toHaveBeenCalledExactlyOnceWith("Moved 2 models to trash", expect.objectContaining({ action: expect.objectContaining({ label: "Undo" }) })));
    await waitFor(() => expect(screen.queryByText(/selected/)).not.toBeInTheDocument());
  });

  it("canceling the delete confirm dialog doesn't POST to /models/bulk-delete", async () => {
    mockGalleryOkWithModels([GALLERY_MODEL]);
    renderLibraryPage();
    await screen.findByText("Test Model");
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Test Model" }));
    await screen.findByText("1 selected");

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = screen.getByRole("dialog");
    // Two "Close" buttons live in a `ConfirmDialog` -- the footer's labeled
    // one (index 0) and the corner icon button (sr-only "Close" text) --
    // same disambiguation as DuplicatesPage.test.tsx.
    fireEvent.click(within(dialog).getAllByRole("button", { name: "Close" })[0]);

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(postMock.mock.calls.some((call: unknown[]) => call[0] === "/models/bulk-delete")).toBe(false);
    // Selection survives closing the dialog without confirming.
    expect(screen.getByText("1 selected")).toBeInTheDocument();
  });

  it("toasts the ApiError detail and keeps the selection when the delete POST rejects", async () => {
    mockGalleryOkWithModels([GALLERY_MODEL]);
    postMock.mockImplementationOnce(() =>
      Promise.reject(new ApiError(409, "model(s) have files still processing: [1]")),
    );
    renderLibraryPage();
    await screen.findByText("Test Model");
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Test Model" }));
    await screen.findByText("1 selected");

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Move to trash" }));

    await waitFor(() =>
      expect(toastErrorMock).toHaveBeenCalledExactlyOnceWith("model(s) have files still processing: [1]"),
    );
    expect(toastSuccessMock).not.toHaveBeenCalled();
    // Action bar still present with the selection intact.
    expect(screen.getByText("1 selected")).toBeInTheDocument();
  });

  function cardLink(name: string): HTMLElement {
    const link = screen.getByText(name).closest("a");
    if (!link) throw new Error(`no card link found for "${name}"`);
    return link;
  }

  it("shift-clicking two cards selects the inclusive range between them, auto-entering select mode", async () => {
    mockGalleryOkWithModels([GALLERY_MODEL, GALLERY_MODEL_2, GALLERY_MODEL_3, GALLERY_MODEL_4]);
    const { router } = renderLibraryPage();
    await screen.findByText("Test Model");

    fireEvent.click(cardLink("Test Model"), { shiftKey: true });
    await screen.findByText("1 selected");

    fireEvent.click(cardLink("Test Model 4"), { shiftKey: true });

    expect(await screen.findByText("4 selected")).toBeInTheDocument();
    // No navigation happened on either modified click.
    expect(router.state.location.pathname).toBe("/library");
  });

  it("ctrl-clicking a card toggles just that card's selection", async () => {
    mockGalleryOkWithModels([GALLERY_MODEL, GALLERY_MODEL_2]);
    const { router } = renderLibraryPage();
    await screen.findByText("Test Model");

    fireEvent.click(cardLink("Test Model"), { ctrlKey: true });
    expect(await screen.findByText("1 selected")).toBeInTheDocument();

    fireEvent.click(cardLink("Test Model"), { ctrlKey: true });
    await waitFor(() => expect(screen.queryByText(/selected/)).not.toBeInTheDocument());
    expect(router.state.location.pathname).toBe("/library");
  });
});

describe("LibraryPage -- virtualized grid (R9-A item 2)", () => {
  it("renders far fewer than 200 cards in the DOM for a 200-item page", async () => {
    const models: ModelSummary[] = Array.from({ length: 200 }, (_, i) => ({
      ...GALLERY_MODEL,
      id: i + 1,
      slug: `model-${i + 1}`,
      name: `Model ${i + 1}`,
    }));
    mockGalleryOkWithModels(models);
    renderLibraryPage();

    await screen.findByText("Model 1");

    const renderedCardTitles = screen.getAllByRole("heading", { level: 3 });
    expect(renderedCardTitles.length).toBeGreaterThan(0);
    expect(renderedCardTitles.length).toBeLessThan(200);
  });

  it("fetches the next page once the last virtual row is reached and more pages exist", async () => {
    getMock.mockImplementation((path: string) => {
      if (path.startsWith("/models")) {
        return Promise.resolve(
          path.includes("cursor=")
            ? { items: [], next_cursor: null }
            : { items: [GALLERY_MODEL, GALLERY_MODEL_2], next_cursor: "page-2" },
        );
      }
      return Promise.resolve([]);
    });
    renderLibraryPage();

    await screen.findByText("Test Model");

    // Two items at the default (2-column) layout is exactly one row --
    // the only, and therefore last, virtual row -- so it should trigger
    // fetchNextPage as soon as it renders.
    await waitFor(() => expect(lastModelsCall()).toContain("cursor=page-2"));
  });

  it("attaches its ResizeObserver on first render and grows past 2 columns once the container is measured wider (fix wave findings 1, 2, 6)", async () => {
    const models: ModelSummary[] = Array.from({ length: 12 }, (_, i) => ({
      ...GALLERY_MODEL,
      id: i + 1,
      slug: `model-${i + 1}`,
      name: `Model ${i + 1}`,
    }));
    mockGalleryOkWithModels(models);
    renderLibraryPage();

    await screen.findByText("Model 1");

    // Finding 1: the observer must be attached on the very first render that
    // has a real grid, not stuck waiting on a skeleton-branch effect that
    // never re-runs.
    expect(resizeObserverTargets.length).toBeGreaterThan(0);

    act(() => fireGridResize(1300));

    // Finding 2: the row's painted `gridTemplateColumns` must come from the
    // SAME `columns` state `chunkIntoRows` used to build the row, so the two
    // can never disagree the way the old viewport-based Tailwind classes did.
    await waitFor(() => {
      const row = document.querySelector('[data-index="0"]') as HTMLElement | null;
      expect(row).not.toBeNull();
      expect(row!.style.gridTemplateColumns).toBe("repeat(5, minmax(0, 1fr))");
    });
  });

  it("does not fetch a next page once hasNextPage is false", async () => {
    mockGalleryOkWithModels([GALLERY_MODEL, GALLERY_MODEL_2]);
    renderLibraryPage();

    await screen.findByText("Test Model");

    const callsMade = getMock.mock.calls.filter((call: unknown[]) => (call[0] as string).startsWith("/models")).length;
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(
      getMock.mock.calls.filter((call: unknown[]) => (call[0] as string).startsWith("/models")).length,
    ).toBe(callsMade);
  });
});

describe("LibraryPage -- keyboard shortcuts (R9-C item 5)", () => {
  it("`/` focuses the search input", async () => {
    mockGalleryOkWithModels([GALLERY_MODEL]);
    renderLibraryPage();
    await screen.findByText("Test Model");

    const search = screen.getByLabelText("Search models");
    expect(search).not.toHaveFocus();

    fireEvent.keyDown(document.body, { key: "/" });
    expect(search).toHaveFocus();
  });

  it("Escape exits select mode and clears the selection", async () => {
    mockGalleryOkWithModels([GALLERY_MODEL]);
    renderLibraryPage();
    await screen.findByText("Test Model");
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Test Model" }));
    await screen.findByText("1 selected");

    fireEvent.keyDown(document.body, { key: "Escape" });

    await waitFor(() => expect(screen.queryByText(/selected/)).not.toBeInTheDocument());
    expect(screen.getByRole("checkbox", { name: "Select Test Model" })).not.toBeChecked();
  });

  it("Escape does nothing to the selection while the bulk-delete confirm dialog is open (fix wave finding 5)", async () => {
    mockGalleryOkWithModels([GALLERY_MODEL]);
    renderLibraryPage();
    await screen.findByText("Test Model");
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Test Model" }));
    await screen.findByText("1 selected");

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await screen.findByRole("dialog");

    // Escape while the ConfirmDialog is open must be left to Radix's own
    // Escape handling (which closes just the dialog) -- the page-level
    // Escape binding used to fire unconditionally and silently discard the
    // whole selection underneath it. Radix's own handler may still close
    // the dialog itself; what must NOT happen is the selection getting
    // wiped along with it.
    fireEvent.keyDown(document.body, { key: "Escape" });

    expect(screen.getByText("1 selected")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Select Test Model" })).toBeChecked();
  });

  it("Delete opens the bulk-delete confirm when there is a selection", async () => {
    mockGalleryOkWithModels([GALLERY_MODEL]);
    renderLibraryPage();
    await screen.findByText("Test Model");
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Test Model" }));
    await screen.findByText("1 selected");

    fireEvent.keyDown(document.body, { key: "Delete" });

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Move 1 model to trash?")).toBeInTheDocument();
  });

  it("Delete does nothing when there is no selection", async () => {
    mockGalleryOkWithModels([GALLERY_MODEL]);
    renderLibraryPage();
    await screen.findByText("Test Model");

    fireEvent.keyDown(document.body, { key: "Delete" });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("mod+a selects every loaded item even with no prior selection", async () => {
    mockGalleryOkWithModels([GALLERY_MODEL, GALLERY_MODEL_2]);
    renderLibraryPage();
    await screen.findByText("Test Model");

    fireEvent.keyDown(document.body, { key: "a", ctrlKey: true });

    expect(await screen.findByText("2 selected")).toBeInTheDocument();
  });

  it("a selects every loaded item once a selection is already active", async () => {
    mockGalleryOkWithModels([GALLERY_MODEL, GALLERY_MODEL_2]);
    renderLibraryPage();
    await screen.findByText("Test Model");
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Test Model" }));
    await screen.findByText("1 selected");

    fireEvent.keyDown(document.body, { key: "a" });

    expect(await screen.findByText("2 selected")).toBeInTheDocument();
  });

  it("a bare 'a' does nothing while there is no active selection", async () => {
    mockGalleryOkWithModels([GALLERY_MODEL, GALLERY_MODEL_2]);
    renderLibraryPage();
    await screen.findByText("Test Model");

    fireEvent.keyDown(document.body, { key: "a" });

    expect(screen.queryByText(/selected/)).not.toBeInTheDocument();
  });
});

describe("LibraryPage -- view mode toggle (R13b)", () => {
  it("defaults to grid view and switches to list view on click", async () => {
    mockGalleryOkWithModels([GALLERY_MODEL]);
    renderLibraryPage();
    await screen.findByText("Test Model");

    expect(screen.getByRole("button", { name: "Grid view" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "List view" })).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(screen.getByRole("button", { name: "List view" }));

    expect(screen.getByRole("button", { name: "List view" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Grid view" })).toHaveAttribute("aria-pressed", "false");
  });

  it("persists the chosen view mode across a remount via localStorage", async () => {
    mockGalleryOkWithModels([GALLERY_MODEL]);
    const { unmount } = renderLibraryPage();
    await screen.findByText("Test Model");

    fireEvent.click(screen.getByRole("button", { name: "List view" }));
    expect(window.localStorage.getItem("library-view")).toBe("list");
    unmount();

    renderLibraryPage();
    await screen.findByText("Test Model");
    expect(screen.getByRole("button", { name: "List view" })).toHaveAttribute("aria-pressed", "true");
  });

  it("switches to folder view, hiding the format/tag facets", async () => {
    mockGalleryOkWithModels([GALLERY_MODEL]);
    renderLibraryPage();
    await screen.findByText("Test Model");

    fireEvent.click(screen.getByRole("button", { name: "Folder view" }));

    expect(screen.queryByRole("group", { name: "Filter by format" })).not.toBeInTheDocument();
  });
});

describe("LibraryPage -- sort options (R13b)", () => {
  it("includes Recently added and Most printed alongside the existing sorts", async () => {
    mockGalleryOk();
    renderLibraryPage();
    await screen.findByText("No models yet");

    const sortSelect = screen.getByLabelText("Sort by");
    const optionLabels = within(sortSelect).getAllByRole("option").map((option) => option.textContent);
    expect(optionLabels).toEqual(["Recently updated", "Recently added", "Most printed", "Name"]);
  });
});

describe("LibraryPage -- category facet (R13b)", () => {
  it("renders categories as chips and filters the gallery query by category=<id>", async () => {
    mockGalleryOkWithCategories([CATEGORY]);
    renderLibraryPage();
    await screen.findByText("No models yet");

    const chip = await screen.findByRole("button", { name: /Miniatures/ });
    fireEvent.click(chip);

    await waitFor(() => expect(lastModelsCall()).toContain("category=3"));
  });

  it("seeds the category filter from a ?category= URL search param on mount", async () => {
    mockGalleryOkWithCategories([CATEGORY]);
    renderLibraryPage(["/library?category=3"]);

    await waitFor(() => expect(lastModelsCall()).toContain("category=3"));
  });

  // Fix-review finding 3: `activeCategory` used to be a one-shot `useState`
  // seed from `search.category`, so a sidebar category link -- which only
  // changes `?category=` on the ALREADY-mounted `/` route (no remount) --
  // never took effect. It's derived straight from `search.category` now, so
  // a search-only navigation (simulating that sidebar link) must update the
  // filter live, and preserve whatever else was already in the URL.
  it("updates the category filter live when ?category= changes without remounting, preserving other search params", async () => {
    mockGalleryOkWithCategories([CATEGORY]);
    const { router } = renderLibraryPage(["/library?collection=5"]);
    await screen.findByText("No models match these filters");

    await act(async () => {
      await router.navigate({
        to: "/library",
        search: (prev: Record<string, unknown>) => ({ ...prev, category: 3 }),
      });
    });

    await waitFor(() => expect(lastModelsCall()).toContain("category=3"));
    expect(router.state.location.search).toMatchObject({ collection: 5, category: 3 });
  });
});
