import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FolderBrowser } from "@/components/gallery/FolderBrowser";
import type { ModelSummary, StorageTreeOut } from "@/api/types";

const { treesBox, getMock, loadMoreMock, moreBox } = vi.hoisted(() => {
  const model: ModelSummary = {
    id: 1,
    slug: "goblin",
    name: "Goblin",
    description: null,
    tags: ["fantasy"],
    updated_at: "2026-06-01T12:00:00Z",
    created_at: "2026-05-01T12:00:00Z",
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
    category: { id: 1, name: "Minis", color: "violet", model_count: 1 },
  };
  const goblinFile = {
    id: 11,
    name: "goblin.stl",
    rel_path: "figures/dnd/goblin/goblin.stl",
    size: 204800,
    kind: "mesh" as const,
    format: "stl" as const,
    model_slug: "goblin",
    blob_hash: "abc123",
    revision_id: 1,
  };
  return {
    treesBox: {
      current: {
        "": {
          path: "",
          dirs: [{ name: "figures", path: "figures", file_count: 4, model_count: 4 }],
          files: [],
          model: null,
        },
        figures: {
          path: "figures",
          dirs: [{ name: "dnd", path: "figures/dnd", file_count: 2, model_count: 2 }],
          files: [],
          model: null,
        },
        "figures/dnd/goblin": {
          path: "figures/dnd/goblin",
          dirs: [],
          files: [goblinFile],
          model,
        },
      } as Record<string, StorageTreeOut>,
    },
    loadMoreMock: vi.fn(),
    moreBox: { hasMore: false },
    getMock: vi.fn((path: string) => Promise.resolve(path === "/tags" ? [] : {})),
  };
});

vi.mock("@/api/storageTree", () => ({
  useStorageTree: (path: string) => ({
    data: treesBox.current[path] ?? { path, dirs: [], files: [], model: null },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
    hasMore: moreBox.hasMore,
    loadingMore: false,
    loadMore: loadMoreMock,
  }),
}));

// `useTagColorMap` (used by the model header strip) goes through the real
// `api` client -- stub it the same way other gallery tests do so it
// resolves harmlessly.
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock },
  };
});

function renderBrowser(path: string, onNavigate = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const rootRoute = createRootRoute();
  const homeRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => <FolderBrowser path={path} onNavigate={onNavigate} />,
  });
  const modelRoute = createRoute({ getParentRoute: () => rootRoute, path: "/models/$slug", component: () => null });
  const router = createRouter({
    routeTree: rootRoute.addChildren([homeRoute, modelRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  return {
    onNavigate,
    router,
    ...render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    ),
  };
}

beforeEach(() => {
  getMock.mockClear();
  loadMoreMock.mockClear();
  moreBox.hasMore = false;
});

describe("FolderBrowser", () => {
  it("renders subfolders as cards with their file/model counts", async () => {
    renderBrowser("");

    expect(await screen.findByText("figures")).toBeInTheDocument();
    expect(screen.getByText("4 files, 4 models")).toBeInTheDocument();
  });

  it("navigates into a subfolder when its card is clicked", async () => {
    const { onNavigate } = renderBrowser("");

    fireEvent.click(await screen.findByText("figures"));

    expect(onNavigate).toHaveBeenCalledExactlyOnceWith("figures");
  });

  it("renders a breadcrumb for the current path", async () => {
    renderBrowser("figures");

    expect(await screen.findByText("dnd")).toBeInTheDocument();
    const breadcrumb = screen.getByRole("navigation", { name: "Folder path" });
    expect(breadcrumb).toHaveTextContent("Library");
    expect(breadcrumb).toHaveTextContent("figures");
  });

  it("navigates to the root when the Library breadcrumb is clicked", async () => {
    const { onNavigate } = renderBrowser("figures");
    await screen.findByText("dnd");

    fireEvent.click(screen.getByRole("button", { name: "Library" }));

    expect(onNavigate).toHaveBeenCalledExactlyOnceWith("");
  });

  it("filters folders and files by the in-folder text filter, client-side", async () => {
    renderBrowser("figures/dnd/goblin");
    await screen.findByText("goblin.stl");

    fireEvent.change(screen.getByLabelText("Filter this folder"), { target: { value: "nomatch" } });

    await waitFor(() => expect(screen.queryByText("goblin.stl")).not.toBeInTheDocument());
  });

  it("shows an empty state when a folder has no subfolders or files", async () => {
    renderBrowser("figures/dnd");

    expect(await screen.findByText("This folder is empty")).toBeInTheDocument();
  });

  it("renders files as rows with a download action and an Open model link", async () => {
    renderBrowser("figures/dnd/goblin");

    expect(await screen.findByText("goblin.stl")).toBeInTheDocument();
    const openModelLinks = screen.getAllByRole("link", { name: "Open model" });
    expect(openModelLinks.length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "Download goblin.stl" })).toHaveAttribute(
      "href",
      "/api/files/11/download",
    );
  });

  it("shows the model header strip (name, category, tags) when the tree response's model is non-null", async () => {
    renderBrowser("figures/dnd/goblin");

    expect(await screen.findByText("Goblin")).toBeInTheDocument();
    expect(screen.getByText("Minis")).toBeInTheDocument();
    expect(screen.getByText("fantasy")).toBeInTheDocument();
  });

  it("offers Load more when the folder has more files than loaded", async () => {
    moreBox.hasMore = true;
    treesBox.current["figures/dnd/goblin"] = {
      ...treesBox.current["figures/dnd/goblin"],
      total_files: 700,
    };
    renderBrowser("figures/dnd/goblin");

    expect(await screen.findByText("1 of 700 files shown")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(loadMoreMock).toHaveBeenCalledOnce();
  });

  it("hides Load more when everything is loaded", async () => {
    renderBrowser("figures/dnd/goblin");
    await screen.findByText("goblin.stl");
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });
});
