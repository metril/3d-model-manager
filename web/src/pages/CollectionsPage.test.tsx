import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CollectionsPage } from "@/pages/CollectionsPage";
import { parseCollectionsSearch } from "@/pages/collectionsSearch";
import type { PendingImport } from "@/api/types";

// `FollowedCard`/`ReviewQueueCard`/`BrowseCard`'s own behavior is covered in
// depth by SavedPanel.test.tsx, and `ImportsPanel`'s by ImportsPanel.test.tsx
// -- here we only prove the page wires its three tabs together correctly
// (default tab, the queue trigger's count badge, and the `?tab=` URL
// contract), so `usePendingImports` is the only hook whose return value
// varies per test.
//
// It's backed by `useSyncExternalStore` (not a bare closure read) so tests
// can simulate a background refetch resolving mid-test via `setPending`
// and have the page actually re-render with the new value -- a plain
// `() => pendingBox.current` mock only ever reflects the value at mount.
const { pendingBox, pendingListeners, setPending } = vi.hoisted(() => {
  const listeners = new Set<() => void>();
  const box = { current: { data: [] as PendingImport[], isLoading: false } };
  return {
    pendingBox: box,
    pendingListeners: listeners,
    setPending: (next: typeof box.current) => {
      box.current = next;
      listeners.forEach((listener) => listener());
    },
  };
});

const idle = { isPending: false, isError: false, error: null };
const empty = { data: [], isLoading: false };

vi.mock("@/api/collections", async () => {
  const React = await vi.importActual<typeof import("react")>("react");
  return {
    useFollowedCollections: () => empty,
    usePendingImports: () =>
      React.useSyncExternalStore(
        (onStoreChange) => {
          pendingListeners.add(onStoreChange);
          return () => {
            pendingListeners.delete(onStoreChange);
          };
        },
        () => pendingBox.current,
      ),
    useRemoteLists: () => empty,
    useSyncCollectionsNow: () => ({ ...idle, mutate: vi.fn() }),
    useApprovePending: () => ({ ...idle, mutate: vi.fn() }),
    useDismissPending: () => ({ ...idle, mutate: vi.fn() }),
    useFollowCollection: () => ({ ...idle, mutate: vi.fn() }),
    useFollowCollectionByUrl: () => ({ ...idle, mutate: vi.fn() }),
    useUnfollowCollection: () => ({ ...idle, mutate: vi.fn() }),
    useSetCollectionMode: () => ({ ...idle, mutate: vi.fn() }),
  };
});

// `ImportsPanel`'s own behavior is covered by ImportsPanel.test.tsx; here it
// just needs to not make a real network call when the page mounts it.
vi.mock("@/api/imports", () => ({
  useImportsList: () => empty,
  useRetryImport: () => ({ ...idle, mutate: vi.fn() }),
  useDismissImport: () => ({ ...idle, mutate: vi.fn() }),
  useDismissFailedImports: () => ({ ...idle, mutate: vi.fn() }),
  useFailedImportsCount: () => 0,
}));

// Radix Select never opens under jsdom -- swap for a native <select>.
vi.mock("@/components/ui/select", () => ({
  Select: ({ children }: { children?: ReactNode }) => <select>{children}</select>,
  SelectTrigger: () => null,
  SelectValue: () => null,
  SelectContent: ({ children }: { children?: ReactNode }) => <>{children}</>,
  SelectItem: ({ value, children }: { value: string; children?: ReactNode }) => (
    <option value={value}>{children}</option>
  ),
}));

// Radix Tabs switches on focus/pointer, not a bare `fireEvent.click`, so tab
// activation is unreliable under jsdom (same limitation SettingsPage.test.tsx
// documents). `CollectionsPage`'s tabs are fully CONTROLLED (`value`/
// `onValueChange`, driven by the URL) rather than SettingsPage's uncontrolled
// `defaultValue`, so this mock just renders the given `value`/`onValueChange`
// through a plain click handler instead of Radix's real activation logic.
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
    }: {
      value: string;
      onValueChange: (value: string) => void;
      children?: ReactNode;
    }) => <TabsCtx.Provider value={{ value, setValue: onValueChange }}>{children}</TabsCtx.Provider>,
    TabsList: ({ children }: { children?: ReactNode }) => <div role="tablist">{children}</div>,
    TabsTrigger: ({
      value,
      children,
      className,
    }: {
      value: string;
      children?: ReactNode;
      className?: string;
    }) => {
      const ctx = React.useContext(TabsCtx);
      return (
        <button
          role="tab"
          type="button"
          aria-selected={ctx.value === value}
          className={className}
          onClick={() => ctx.setValue(value)}
        >
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

function fakePending(overrides: Partial<PendingImport> = {}): PendingImport {
  return {
    id: 11,
    collection_id: 1,
    site: "makerworld",
    external_id: "42",
    title: "Cable clip",
    url: "https://makerworld.com/en/models/42",
    thumbnail_url: null,
    created_at: "2026-07-09T00:00:00Z",
    group_collection_id: 1,
    group_title: "Desk stuff",
    ...overrides,
  };
}

// Mirrors the real route tree's shape (`authenticatedRoute` is a pathless
// layout route with `id: "authenticated"`, `collectionsRoute` is its
// `/collections` child) since `CollectionsPage` reads/writes its tab via
// `getRouteApi("/authenticated/collections")`, which resolves by that exact
// route id at runtime.
function renderPage(initialEntries: string[] = ["/collections"]) {
  const rootRoute = createRootRoute();
  const authenticatedRoute = createRoute({ id: "authenticated", getParentRoute: () => rootRoute });
  const collectionsRoute = createRoute({
    getParentRoute: () => authenticatedRoute,
    path: "/collections",
    validateSearch: parseCollectionsSearch,
    component: CollectionsPage,
  });
  const settingsRoute = createRoute({
    getParentRoute: () => authenticatedRoute,
    path: "/settings",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([authenticatedRoute.addChildren([collectionsRoute, settingsRoute])]),
    history: createMemoryHistory({ initialEntries }),
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
  pendingBox.current = { data: [], isLoading: false };
});

describe("CollectionsPage", () => {
  it("renders the Collections heading and three tab triggers", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: "Collections" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Collections" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Review queue/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Recent imports/ })).toBeInTheDocument();
  });

  it("defaults to the Collections tab when nothing is pending", async () => {
    renderPage();

    expect(await screen.findByRole("tab", { name: "Collections" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText("Followed collections")).toBeInTheDocument();
    expect(screen.getByText("Your collections on each site")).toBeInTheDocument();
  });

  it("defaults to the Review queue tab when pending items exist", async () => {
    pendingBox.current = { data: [fakePending()], isLoading: false };
    renderPage();

    expect(await screen.findByRole("tab", { name: /Review queue/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByTestId("review-queue")).toBeInTheDocument();
    expect(screen.queryByText("Followed collections")).not.toBeInTheDocument();
  });

  it("shows the pending count as a badge on the Review queue tab trigger", async () => {
    pendingBox.current = { data: [fakePending({ id: 11 }), fakePending({ id: 12 })], isLoading: false };
    renderPage();

    expect(await screen.findByRole("tab", { name: /Review queue/ })).toHaveTextContent("2");
  });

  it("omits the count badge from the Review queue tab trigger when nothing is pending", async () => {
    renderPage(["/collections?tab=review"]);

    expect(await screen.findByRole("tab", { name: "Review queue" })).toBeInTheDocument();
  });

  it("renders ImportsPanel's content when the URL requests ?tab=imports", async () => {
    renderPage(["/collections?tab=imports"]);

    expect(await screen.findByRole("tab", { name: /Recent imports/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      await screen.findByText(/No imports yet\. Save a model from the extension/),
    ).toBeInTheDocument();
  });

  it("updates the ?tab= URL param when switching tabs, without touching other tabs' content", async () => {
    const router = renderPage(["/collections"]);

    fireEvent.click(await screen.findByRole("tab", { name: /Recent imports/ }));

    await waitFor(() => expect(router.state.location.search).toEqual({ tab: "imports" }));
    expect(
      await screen.findByText(/No imports yet\. Save a model from the extension/),
    ).toBeInTheDocument();
  });

  // Regression coverage for the R7 fix: the default tab used to be recomputed
  // from `pending.data` on every render, so a background refetch that
  // resolved with a different pending count could move the user off a tab
  // they were already on -- either a tab they'd explicitly clicked into, or
  // the pinned no-`?tab=` default. Both cases below simulate that refetch via
  // `setPending`, which (unlike reassigning `pendingBox.current` before
  // `renderPage`) notifies the mounted component through the mocked
  // `useSyncExternalStore`-backed `usePendingImports`.

  it("does not move the user off a tab they explicitly selected when the pending query changes", async () => {
    pendingBox.current = { data: [fakePending()], isLoading: false };
    const router = renderPage(["/collections"]);

    // Defaults to Review queue since an item is already pending.
    expect(await screen.findByRole("tab", { name: /Review queue/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    // User explicitly switches to Collections, which always wins via `?tab=`.
    fireEvent.click(await screen.findByRole("tab", { name: "Collections" }));
    await waitFor(() => expect(router.state.location.search).toEqual({ tab: "collections" }));

    // A background refetch resolves with more pending items -- must not yank
    // the user back to Review queue.
    setPending({ data: [fakePending({ id: 11 }), fakePending({ id: 12 })], isLoading: false });

    expect(await screen.findByRole("tab", { name: /Review queue/ })).toHaveTextContent("2");
    expect(screen.getByRole("tab", { name: "Collections" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Followed collections")).toBeInTheDocument();
  });

  it("does not change the pinned default tab when the pending query refetches in the background", async () => {
    const router = renderPage(["/collections"]);

    // No `?tab=` and nothing pending yet -> pins to Collections.
    expect(await screen.findByRole("tab", { name: "Collections" })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    // A background refetch (e.g. window refocus after a sync) resolves with
    // newly-pending items. The pinned default must not move to Review queue.
    setPending({ data: [fakePending()], isLoading: false });

    expect(await screen.findByRole("tab", { name: /Review queue/ })).toHaveTextContent("1");
    expect(screen.getByRole("tab", { name: "Collections" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Followed collections")).toBeInTheDocument();
    expect(router.state.location.search).toEqual({});
  });
});
