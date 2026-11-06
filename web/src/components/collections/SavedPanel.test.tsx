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
import { BrowseCard, FollowedCard, ReviewQueueCard } from "@/components/collections/SavedPanel";
import type { FollowedCollection, PendingImport, RemoteList } from "@/api/types";

const {
  followedBox,
  pendingBox,
  remoteListsBox,
  syncNowMock,
  approveMock,
  dismissMock,
  followMock,
  unfollowMock,
  setModeMock,
  postMock,
} = vi.hoisted(() => ({
  followedBox: { current: { data: [] as FollowedCollection[], isLoading: false } },
  pendingBox: { current: { data: [] as PendingImport[], isLoading: false } },
  remoteListsBox: { current: { data: [] as RemoteList[], isLoading: false } },
  syncNowMock: vi.fn(),
  approveMock: vi.fn(),
  dismissMock: vi.fn(),
  followMock: vi.fn(),
  unfollowMock: vi.fn(),
  setModeMock: vi.fn(),
  postMock: vi.fn(),
}));

const idle = { isPending: false, isError: false, error: null };

// `useFollowCollectionByUrl` is deliberately left as the real implementation
// (via `importOriginal`) rather than mocked like the others -- the tests
// below need to see the actual POST it makes, so only `@/api/client`'s
// `post` is stubbed (same pattern as BrowserExtensionCard.test.tsx).
vi.mock("@/api/collections", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/collections")>();
  return {
    ...actual,
    useFollowedCollections: () => followedBox.current,
    usePendingImports: () => pendingBox.current,
    useRemoteLists: () => remoteListsBox.current,
    useSyncCollectionsNow: () => ({ ...idle, mutate: syncNowMock }),
    useApprovePending: () => ({ ...idle, mutate: approveMock }),
    useDismissPending: () => ({ ...idle, mutate: dismissMock }),
    useFollowCollection: () => ({ ...idle, mutate: followMock }),
    useUnfollowCollection: () => ({ ...idle, mutate: unfollowMock }),
    useSetCollectionMode: () => ({ ...idle, mutate: setModeMock }),
  };
});

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, post: postMock },
  };
});

// Radix Select never opens under jsdom -- swap for a native <select>.
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
    <select aria-label="Sync mode" value={value} onChange={(e) => onValueChange(e.target.value)}>
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

function fakeFollowed(overrides: Partial<FollowedCollection> = {}): FollowedCollection {
  return {
    id: 1,
    site: "makerworld",
    list_id: "7",
    kind: "collection",
    title: "Desk stuff",
    mode: "review",
    last_synced_at: null,
    last_error: null,
    created_at: "2026-07-09T00:00:00Z",
    preview_thumbnails: [],
    ...overrides,
  };
}

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

// Each card is now mounted standalone (R7 T2 split `SavedPanel` into
// `FollowedCard`/`ReviewQueueCard`/`BrowseCard`, placed under different tabs
// by `CollectionsPage`) -- still wrapped in a router since `BrowseCard`
// links to `/settings`.
function renderCard(Component: () => ReactNode) {
  const rootRoute = createRootRoute();
  const home = createRoute({ getParentRoute: () => rootRoute, path: "/", component: Component });
  const settings = createRoute({
    getParentRoute: () => rootRoute,
    path: "/settings",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([home, settings]),
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
  vi.clearAllMocks();
  followedBox.current = { data: [], isLoading: false };
  pendingBox.current = { data: [], isLoading: false };
  remoteListsBox.current = { data: [], isLoading: false };
  postMock.mockReset();
  window.localStorage.clear();
});

describe("FollowedCard", () => {
  it("runs a sync on demand", async () => {
    renderCard(FollowedCard);
    fireEvent.click(await screen.findByRole("button", { name: /Sync now/ }));
    expect(syncNowMock).toHaveBeenCalled();
  });

  it("lists a followed collection and switches its sync mode", async () => {
    followedBox.current = { data: [fakeFollowed()], isLoading: false };
    renderCard(FollowedCard);

    expect(await screen.findByText("Desk stuff")).toBeInTheDocument();
    expect(screen.getByText(/never synced/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Sync mode"), { target: { value: "auto" } });
    expect(setModeMock).toHaveBeenCalledWith({ id: 1, mode: "auto" });

    fireEvent.click(screen.getByRole("button", { name: "Unfollow" }));
    expect(unfollowMock).not.toHaveBeenCalled();
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Unfollow" }));
    expect(unfollowMock).toHaveBeenCalledWith(1);
  });

  it("renders a 2x2 collage of the collection's preview thumbnails with empty tiles for the rest", async () => {
    followedBox.current = {
      data: [fakeFollowed({ preview_thumbnails: ["/a.jpg", "/b.jpg"] })],
      isLoading: false,
    };
    renderCard(FollowedCard);

    await screen.findByText("Desk stuff");
    const images = screen.getAllByTestId("collage-thumb");
    expect(images).toHaveLength(2);
    expect(images.map((img) => img.getAttribute("src"))).toEqual(["/a.jpg", "/b.jpg"]);
    for (const img of images) {
      expect(img).toHaveAttribute("loading", "lazy");
    }
  });

  it("links 'Download ZIP' straight at the collection's zip endpoint with `download`", async () => {
    followedBox.current = { data: [fakeFollowed()], isLoading: false };
    renderCard(FollowedCard);

    const link = await screen.findByRole("link", { name: /Download Desk stuff as zip/i });
    expect(link).toHaveAttribute("href", "/api/collections/1/zip");
    expect(link).toHaveAttribute("download");
  });
});

describe("ReviewQueueCard", () => {
  it("shows queued review items and imports or dismisses one", async () => {
    pendingBox.current = { data: [fakePending()], isLoading: false };
    renderCard(ReviewQueueCard);

    const queue = within(await screen.findByTestId("review-queue"));
    expect(queue.getByText("Cable clip")).toBeInTheDocument();

    fireEvent.click(queue.getByRole("button", { name: "Import" }));
    expect(approveMock).toHaveBeenCalledWith(11);

    fireEvent.click(queue.getByRole("button", { name: "Dismiss" }));
    expect(dismissMock).toHaveBeenCalledWith(11);
  });

  it("groups review items by the server-resolved group_title, ordered by title, splitting items that share a legacy collection_id into different groups", async () => {
    pendingBox.current = {
      data: [
        fakePending({ id: 11, collection_id: 1, group_collection_id: 1, group_title: "Desk stuff", title: "Cable clip" }),
        // Same legacy `collection_id` (1) as "Cable clip" above, but a
        // different `group_collection_id` -- these must land in different
        // groups even though the old client-side join (by `collection_id`)
        // would have merged them.
        fakePending({ id: 12, collection_id: 1, group_collection_id: 2, group_title: "All collected models", title: "Vase" }),
        fakePending({ id: 13, collection_id: 2, group_collection_id: 2, group_title: "All collected models", title: "Planter" }),
        // Empty `group_title` -- shouldn't happen given the backend field is
        // required, but exercises the defensive fallback anyway.
        fakePending({ id: 14, collection_id: 99, group_collection_id: 99, group_title: "", title: "Orphaned thing" }),
      ],
      isLoading: false,
    };
    renderCard(ReviewQueueCard);

    const queue = within(await screen.findByTestId("review-queue"));
    const groupHeadings = queue.getAllByRole("heading", { level: 3 });
    expect(groupHeadings).toHaveLength(3);

    // Sorted alphabetically by title: "All collected models" (2 items) <
    // "Collection #99" (fallback for the missing title) < "Desk stuff" (1
    // item).
    expect(groupHeadings[0]).toHaveTextContent("All collected models");
    expect(groupHeadings[0]).toHaveTextContent("2");
    expect(groupHeadings[1]).toHaveTextContent("Collection #99");
    expect(groupHeadings[2]).toHaveTextContent("Desk stuff");

    expect(queue.getByText("Cable clip")).toBeInTheDocument();
    expect(queue.getByText("Vase")).toBeInTheDocument();
    expect(queue.getByText("Planter")).toBeInTheDocument();
    expect(queue.getByText("Orphaned thing")).toBeInTheDocument();
  });

  it("collapses and re-expands a single group's grid without touching other groups", async () => {
    pendingBox.current = {
      data: [
        fakePending({ id: 11, group_collection_id: 1, group_title: "Desk stuff", title: "Cable clip" }),
        fakePending({ id: 12, group_collection_id: 2, group_title: "Patio parts", title: "Planter" }),
      ],
      isLoading: false,
    };
    renderCard(ReviewQueueCard);

    await screen.findByTestId("review-queue");
    const deskToggle = screen.getByRole("button", { name: /Desk stuff/ });
    const patioToggle = screen.getByRole("button", { name: /Patio parts/ });
    expect(deskToggle).toHaveAttribute("aria-expanded", "true");
    expect(patioToggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Cable clip")).toBeInTheDocument();
    expect(screen.getByText("Planter")).toBeInTheDocument();

    fireEvent.click(deskToggle);
    expect(deskToggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Cable clip")).not.toBeInTheDocument();
    // The other group is untouched: still open, its card still rendered.
    expect(patioToggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Planter")).toBeInTheDocument();

    fireEvent.click(deskToggle);
    expect(deskToggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Cable clip")).toBeInTheDocument();
  });

  it("collapses and re-expands every review group via the expand/collapse-all controls, disabling at the all-open/all-closed boundaries", async () => {
    pendingBox.current = {
      data: [
        fakePending({ id: 11, group_collection_id: 1, group_title: "Desk stuff", title: "Cable clip" }),
        fakePending({ id: 12, group_collection_id: 2, group_title: "Patio parts", title: "Planter" }),
      ],
      isLoading: false,
    };
    renderCard(ReviewQueueCard);

    await screen.findByTestId("review-queue");
    const expandAll = screen.getByRole("button", { name: "Expand all review groups" });
    const collapseAll = screen.getByRole("button", { name: "Collapse all review groups" });
    // Every group defaults open, so expand-all starts disabled and
    // collapse-all starts enabled.
    expect(expandAll).toBeDisabled();
    expect(collapseAll).not.toBeDisabled();

    fireEvent.click(collapseAll);
    expect(screen.getByRole("button", { name: /Desk stuff/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: /Patio parts/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Cable clip")).not.toBeInTheDocument();
    expect(screen.queryByText("Planter")).not.toBeInTheDocument();
    expect(collapseAll).toBeDisabled();
    expect(expandAll).not.toBeDisabled();

    fireEvent.click(expandAll);
    expect(screen.getByRole("button", { name: /Desk stuff/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: /Patio parts/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Cable clip")).toBeInTheDocument();
    expect(screen.getByText("Planter")).toBeInTheDocument();
    expect(expandAll).toBeDisabled();
    expect(collapseAll).not.toBeDisabled();
  });

  it("renders an aligned Import/Dismiss actions row for every review card", async () => {
    pendingBox.current = {
      data: [
        fakePending({ id: 11, title: "Cable clip" }),
        fakePending({
          id: 12,
          title: "A much longer title that would otherwise push its buttons out of line",
        }),
      ],
      isLoading: false,
    };
    renderCard(ReviewQueueCard);

    const queue = within(await screen.findByTestId("review-queue"));
    const actionRows = queue.getAllByTestId("review-item-actions");
    expect(actionRows).toHaveLength(2);
    for (const row of actionRows) {
      expect(within(row).getByRole("button", { name: "Import" })).toBeInTheDocument();
      expect(within(row).getByRole("button", { name: "Dismiss" })).toBeInTheDocument();
    }
  });
});

describe("BrowseCard", () => {
  it("explains how to connect each site when no collections are found", async () => {
    renderCard(BrowseCard);
    expect(await screen.findByText(/No collections found yet/)).toBeInTheDocument();
    expect(screen.getByText(/paste your web\s+token/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /settings/i })).toBeInTheDocument();
  });

  it("also points to the browser extension for MakerWorld collection sync in the empty state", async () => {
    renderCard(BrowseCard);
    expect(
      await screen.findByText(/Your MakerWorld collections sync from the browser extension/),
    ).toBeInTheDocument();
  });

  it("follows a browsable remote list", async () => {
    remoteListsBox.current = {
      data: [{ site: "thingiverse", list_id: "likes", kind: "likes", title: "Likes", count: 3 }],
      isLoading: false,
    };
    renderCard(BrowseCard);

    // Scoped to the remote-lists row -- the "Add collection by URL" form
    // above it also has a button named "Follow".
    const row = within(await screen.findByTestId("remote-lists"));
    fireEvent.click(row.getByRole("button", { name: "Follow" }));
    expect(followMock).toHaveBeenCalledWith({
      site: "thingiverse",
      list_id: "likes",
      kind: "likes",
      title: "Likes",
      mode: "review",
    });
  });

  it("follows a collection pasted as a URL and clears the input on success", async () => {
    postMock.mockResolvedValueOnce({
      id: 5,
      site: "makerworld",
      list_id: "555",
      kind: "collection",
      title: "Some collection",
      mode: "review",
      last_synced_at: null,
      last_error: null,
      created_at: "2026-07-11T00:00:00Z",
    });
    renderCard(BrowseCard);

    const input = await screen.findByPlaceholderText(/makerworld\.com/);
    fireEvent.change(input, { target: { value: "https://makerworld.com/en/collections/555" } });
    fireEvent.click(within(screen.getByTestId("add-collection-by-url")).getByRole("button", { name: "Follow" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/collections/from-url", {
        url: "https://makerworld.com/en/collections/555",
      }),
    );
    await waitFor(() => expect(input).toHaveValue(""));
  });

  it("shows the API's error detail inline when following by URL fails", async () => {
    postMock.mockRejectedValueOnce(new ApiError(422, "Unsupported or invalid URL"));
    renderCard(BrowseCard);

    fireEvent.change(await screen.findByPlaceholderText(/makerworld\.com/), {
      target: { value: "https://example.com/not-a-collection" },
    });
    fireEvent.click(within(screen.getByTestId("add-collection-by-url")).getByRole("button", { name: "Follow" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Unsupported or invalid URL");
  });
});
