import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ModelDetailPage } from "@/pages/ModelDetailPage";
import type { ModelDetail } from "@/api/types";

// This test is only about `ModelDetailPage`'s composition -- which card goes
// where, and in what order -- not any card's internals (each already has its
// own render tests via its wrapped `*Tab`). Every heavy child is stubbed to a
// single identifiable `data-testid` div.
const MODEL: ModelDetail = {
  id: 1,
  slug: "articulated-dragon",
  name: "Articulated Dragon",
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
  current_revision: null,
  notes: [],
  backends: [],
  favorite: false,
  print_count: 0,
  last_printed_at: null,
  metadata: null,
  print_tips: null,
};

vi.mock("@/api/library", () => ({
  useModel: () => ({ isLoading: false, isError: false, data: MODEL }),
}));

vi.mock("@/components/model-detail/ModelHeader", () => ({
  ModelHeader: ({ onToggleEditMode }: { onToggleEditMode: () => void }) => (
    <button type="button" data-testid="model-header" onClick={onToggleEditMode}>
      toggle edit
    </button>
  ),
}));
vi.mock("@/components/model-detail/ArchivedBanner", () => ({
  ArchivedBanner: () => <div data-testid="archived-banner" />,
}));
vi.mock("@/components/model-detail/StudioWorkspace", () => ({
  StudioWorkspace: () => <div data-testid="card-studio" />,
}));
vi.mock("@/components/model-detail/RelatedModels", () => ({
  RelatedModels: () => <div data-testid="card-related" />,
}));
vi.mock("@/components/model-detail/cards/DescriptionCard", () => ({
  DescriptionCard: ({ editMode }: { editMode: boolean }) => (
    <div data-testid="card-description" data-edit={String(editMode)} />
  ),
}));
vi.mock("@/components/model-detail/cards/TagsLinksCard", () => ({
  TagsLinksCard: () => <div data-testid="card-tags-links" />,
}));
vi.mock("@/components/model-detail/cards/GcodeProfilesCard", () => ({
  GcodeProfilesCard: () => <div data-testid="card-gcode-profiles" />,
}));
vi.mock("@/components/model-detail/cards/PrintHistoryCard", () => ({
  PrintHistoryCard: () => <div data-testid="card-print-history" />,
}));
vi.mock("@/components/model-detail/cards/FilesDocsCard", () => ({
  FilesDocsCard: () => <div data-testid="card-files-docs" />,
}));
vi.mock("@/components/model-detail/cards/RevisionsCard", () => ({
  RevisionsCard: () => <div data-testid="card-revisions" />,
}));
vi.mock("@/components/model-detail/cards/NotesCard", () => ({
  NotesCard: () => <div data-testid="card-notes" />,
}));
vi.mock("@/components/model-detail/cards/PrintTipsCard", () => ({
  PrintTipsCard: () => <div data-testid="card-print-tips" />,
}));
vi.mock("@/components/model-detail/cards/SpecsCard", () => ({
  SpecsCard: () => <div data-testid="card-specs" />,
}));

function renderPage() {
  const rootRoute = createRootRoute();
  const authenticatedRoute = createRoute({ id: "authenticated", getParentRoute: () => rootRoute });
  const detailRoute = createRoute({
    getParentRoute: () => authenticatedRoute,
    path: "/models/$slug",
    component: ModelDetailPage,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([authenticatedRoute.addChildren([detailRoute])]),
    history: createMemoryHistory({ initialEntries: ["/models/articulated-dragon"] }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("ModelDetailPage -- composition (R13a DetailLayout)", () => {
  it("renders ModelHeader, then ArchivedBanner, then the two-column card layout", async () => {
    renderPage();

    expect(await screen.findByTestId("model-header")).toBeInTheDocument();
    expect(screen.getByTestId("archived-banner")).toBeInTheDocument();
  });

  it("orders the left column: studio, description, tags & links, related models", async () => {
    renderPage();
    await screen.findByTestId("model-header");

    const ids = Array.from(document.querySelectorAll("[data-testid^='card-']")).map((el) =>
      el.getAttribute("data-testid"),
    );
    const leftOrder = ["card-studio", "card-description", "card-tags-links", "card-related"];
    const leftIndexes = leftOrder.map((id) => ids.indexOf(id));
    expect(leftIndexes).toEqual([...leftIndexes].sort((a, b) => a - b));
    expect(leftIndexes.every((i) => i !== -1)).toBe(true);
  });

  it("orders the right column: g-code profiles, print history, files/docs, revisions, notes, print tips, specs", async () => {
    renderPage();
    await screen.findByTestId("model-header");

    const ids = Array.from(document.querySelectorAll("[data-testid^='card-']")).map((el) =>
      el.getAttribute("data-testid"),
    );
    const rightOrder = [
      "card-gcode-profiles",
      "card-print-history",
      "card-files-docs",
      "card-revisions",
      "card-notes",
      "card-print-tips",
      "card-specs",
    ];
    const rightIndexes = rightOrder.map((id) => ids.indexOf(id));
    expect(rightIndexes).toEqual([...rightIndexes].sort((a, b) => a - b));
    expect(rightIndexes.every((i) => i !== -1)).toBe(true);
  });

  it("passes the page's edit-mode toggle through context to DescriptionCard", async () => {
    renderPage();
    const toggle = await screen.findByTestId("model-header");
    expect(screen.getByTestId("card-description")).toHaveAttribute("data-edit", "false");

    fireEvent.click(toggle);

    expect(screen.getByTestId("card-description")).toHaveAttribute("data-edit", "true");
  });
});
