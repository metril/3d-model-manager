import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProvenanceBlock } from "@/components/model-detail/ProvenanceBlock";
import type { ModelDetail } from "@/api/types";

const BASE_MODEL: ModelDetail = {
  id: 1,
  slug: "articulated-dragon",
  name: "Articulated Dragon",
  description: null,
  source_url: "https://www.thingiverse.com/thing:123",
  source_site: "thingiverse",
  source_author: "someartist",
  source_license: "CC-BY",
  source_collection_id: null,
  source_collection_title: null,
  imported_at: "2026-06-01T12:00:00Z",
  cover_blob_hash: null,
  is_archived: false,
  created_at: "2026-06-01T12:00:00Z",
  updated_at: "2026-06-01T12:00:00Z",
  tags: [],
  current_revision: null,
  notes: [],
  backends: [],
  favorite: false,
  print_count: 0,
  last_printed_at: null,
  metadata: null,
  print_tips: null,
};

function renderBlock(model: ModelDetail) {
  const rootRoute = createRootRoute();
  const homeRoute = createRoute({ getParentRoute: () => rootRoute, path: "/library", component: () => null });
  const detailRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/models/$slug",
    component: () => <ProvenanceBlock model={model} />,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([homeRoute, detailRoute]),
    history: createMemoryHistory({ initialEntries: [`/models/${model.slug}`] }),
  });
  return render(<RouterProvider router={router} />);
}

describe("ProvenanceBlock", () => {
  it("links the collection title to /?collection=<id> when the collection is still followed", async () => {
    renderBlock({ ...BASE_MODEL, source_collection_id: 7, source_collection_title: "Dragons I like" });

    const link = await screen.findByRole("link", { name: "Dragons I like" });
    expect(link).toHaveAttribute("href", "/library?collection=7");
    expect(screen.getByTestId("provenance")).toHaveTextContent("from collection Dragons I like");
  });

  it("renders the collection title as plain text when the collection was unfollowed (id null)", async () => {
    renderBlock({ ...BASE_MODEL, source_collection_id: null, source_collection_title: "Dragons I like" });

    expect(await screen.findByText("Dragons I like")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Dragons I like" })).not.toBeInTheDocument();
  });

  it("shows no collection line when source_collection_title is null", async () => {
    renderBlock(BASE_MODEL);

    const provenance = await screen.findByTestId("provenance");
    expect(provenance).not.toHaveTextContent("from collection");
  });
});
