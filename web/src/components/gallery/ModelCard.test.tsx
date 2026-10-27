import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ModelCard } from "@/components/gallery/ModelCard";
import type { FileOut, ModelSummary } from "@/api/types";

// `usePatchModel` (the "Needs review" dismiss control) calls `api.patch`;
// spy on it so the dismiss test can assert the request, and so the whole
// card renders under a real QueryClient (the mutation hook needs one).
const { patchMock, getMock } = vi.hoisted(() => ({
  patchMock: vi.fn().mockResolvedValue({}),
  getMock: vi.fn((path: string) => Promise.resolve(path === "/tags" ? [] : {})),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, patch: patchMock, get: getMock },
  };
});

const MODEL: ModelSummary = {
  id: 1,
  slug: "articulated-dragon",
  name: "Articulated Dragon",
  description: "A flexible print-in-place dragon",
  tags: ["fantasy", "articulated", "dragon", "flexi"],
  updated_at: "2026-06-01T12:00:00Z",
  created_at: "2026-05-01T12:00:00Z",
  file_count: 3,
  formats: ["stl", "3mf"],
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

function renderCard(
  model: ModelSummary,
  cardProps: {
    selected?: boolean;
    onSelectChange?: (id: number, next: boolean) => void;
    index?: number;
    onModifiedClick?: (event: React.MouseEvent, index: number) => void;
  } = {},
) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const rootRoute = createRootRoute();
  const cardRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => <ModelCard model={model} {...cardProps} />,
  });
  const detailRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/models/$slug",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([cardRoute, detailRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  return {
    router,
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    ),
  };
}

beforeEach(() => {
  patchMock.mockClear();
  getMock.mockClear();
});

describe("ModelCard", () => {
  it("renders name, first 2 tags with overflow count, format badges, and updated date", async () => {
    renderCard(MODEL);

    expect(await screen.findByText("Articulated Dragon")).toBeInTheDocument();

    // First 2 tags shown, rest collapsed into an overflow badge.
    expect(screen.getByText("fantasy")).toBeInTheDocument();
    expect(screen.getByText("articulated")).toBeInTheDocument();
    expect(screen.queryByText("dragon")).not.toBeInTheDocument();
    expect(screen.queryByText("flexi")).not.toBeInTheDocument();
    expect(screen.getByText("+2")).toBeInTheDocument();

    // Format badges (the placeholder thumbnail also shows a "STL" monogram,
    // so scope the query to the badge row to avoid ambiguity).
    const formatBadges = within(screen.getByTestId("format-badges"));
    expect(formatBadges.getByText("STL")).toBeInTheDocument();
    expect(formatBadges.getByText("3MF")).toBeInTheDocument();

    expect(screen.getByText(/Updated/)).toBeInTheDocument();
  });

  it("shows all tags with no overflow badge when there are 2 or fewer", async () => {
    renderCard({ ...MODEL, tags: ["fantasy", "dragon"] });

    expect(await screen.findByText("fantasy")).toBeInTheDocument();
    expect(screen.getByText("dragon")).toBeInTheDocument();
    expect(screen.queryByText(/^\+\d+$/)).not.toBeInTheDocument();
  });

  it("shows a Sliced marker and humanized print time in the spec row when both are set", async () => {
    renderCard({ ...MODEL, has_sliced: true, print_time_s: 5400 });

    const spec = within(await screen.findByTestId("model-spec"));
    expect(spec.getByText("Sliced")).toBeInTheDocument();
    expect(spec.getByText("1h 30m")).toBeInTheDocument();
  });

  it("shows the print time but no Sliced marker when the model isn't sliced", async () => {
    renderCard({ ...MODEL, has_sliced: false, print_time_s: 2700 });

    const spec = within(await screen.findByTestId("model-spec"));
    expect(spec.queryByText("Sliced")).not.toBeInTheDocument();
    expect(spec.getByText("45m")).toBeInTheDocument();
  });

  it("omits print time and the Sliced marker when the model has neither", async () => {
    renderCard(MODEL);

    const spec = within(await screen.findByTestId("model-spec"));
    expect(spec.queryByText("Sliced")).not.toBeInTheDocument();
    // the file count is still shown (spec row renders only present fields)
    expect(spec.getByText("3 files")).toBeInTheDocument();
  });

  it("shows a source badge when the model was imported, none for a manual model", async () => {
    renderCard({ ...MODEL, source_site: "thingiverse" });

    expect(await screen.findByTestId("source-badge")).toHaveTextContent("thingiverse");
  });

  it("renders no source badge for a manually-created model", async () => {
    renderCard(MODEL);

    expect(await screen.findByText("Articulated Dragon")).toBeInTheDocument();
    expect(screen.queryByTestId("source-badge")).not.toBeInTheDocument();
  });

  it("shows a dismissable 'Needs review' badge for an adopted model", async () => {
    renderCard({ ...MODEL, review_state: "adopted" });

    expect(await screen.findByTestId("review-badge")).toHaveTextContent("Needs review");
    expect(screen.getByRole("button", { name: "Dismiss needs review" })).toBeInTheDocument();
  });

  it("renders no 'Needs review' badge for a model that isn't adopted", async () => {
    renderCard(MODEL);

    expect(await screen.findByText("Articulated Dragon")).toBeInTheDocument();
    expect(screen.queryByTestId("review-badge")).not.toBeInTheDocument();
  });

  it("clicking the dismiss control opens a confirm dialog instead of patching immediately, without navigating", async () => {
    const { router } = renderCard({ ...MODEL, review_state: "adopted" });

    fireEvent.click(await screen.findByRole("button", { name: "Dismiss needs review" }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText('Clear "needs review"?')).toBeInTheDocument();
    expect(patchMock).not.toHaveBeenCalled();
    // The trigger sits inside the card's whole-surface <Link>; the click
    // must not have leaked into it (detailRoute renders null, so the card
    // would also unmount if it had).
    expect(router.state.location.pathname).toBe("/");
    expect(screen.getByText("Articulated Dragon")).toBeInTheDocument();
  });

  it("confirming the dialog PATCHes review_state to null without navigating", async () => {
    const { router } = renderCard({ ...MODEL, review_state: "adopted" });

    fireEvent.click(await screen.findByRole("button", { name: "Dismiss needs review" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Clear" }));

    await waitFor(() => expect(patchMock).toHaveBeenCalledTimes(1));
    expect(patchMock).toHaveBeenCalledWith("/models/articulated-dragon", { review_state: null });
    // The confirm button is portaled but bubbles through the React tree
    // into the card's <Link>; the stopCardNavigation wrapper must have
    // swallowed the click before it navigated.
    expect(router.state.location.pathname).toBe("/");
    expect(screen.getByText("Articulated Dragon")).toBeInTheDocument();
  });

  it("shows an outline favorite star for a non-favorited model, filled for a favorited one", async () => {
    renderCard(MODEL);
    const star = await screen.findByRole("button", { name: "Add to favorites" });
    expect(star).toHaveAttribute("aria-pressed", "false");

    renderCard({ ...MODEL, favorite: true });
    const filledStar = await screen.findByRole("button", { name: "Remove from favorites" });
    expect(filledStar).toHaveAttribute("aria-pressed", "true");
  });

  it("clicking the star PATCHes the toggled favorite value without navigating", async () => {
    const { router } = renderCard(MODEL);

    fireEvent.click(await screen.findByRole("button", { name: "Add to favorites" }));

    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledExactlyOnceWith("/models/articulated-dragon", { favorite: true }),
    );
    expect(router.state.location.pathname).toBe("/");
    expect(screen.getByText("Articulated Dragon")).toBeInTheDocument();
  });

  it("always mounts a select checkbox (hover/selected-only visibility), and calls onSelectChange without navigating", async () => {
    const onSelectChange = vi.fn();
    const { router } = renderCard(MODEL, { selected: false, onSelectChange });

    const checkbox = await screen.findByRole("checkbox", { name: "Select Articulated Dragon" });
    expect(checkbox).not.toBeChecked();

    fireEvent.click(checkbox);

    expect(onSelectChange).toHaveBeenCalledExactlyOnceWith(1, true);
    expect(router.state.location.pathname).toBe("/");
  });

  it("renders the checkbox as checked when selected", async () => {
    renderCard(MODEL, { selected: true, onSelectChange: vi.fn() });

    expect(await screen.findByRole("checkbox", { name: "Select Articulated Dragon" })).toBeChecked();
  });
});

describe("ModelCard -- photo-first cover with a render-on-hover (feat/import-fidelity T4)", () => {
  it("renders a second (hover) image when render_url differs from cover", async () => {
    renderCard({ ...MODEL, cover: "/covers/1.jpg", render_url: "/renders/1.png" });

    await screen.findByText("Articulated Dragon");
    expect(screen.getByTestId("render-hover-img")).toBeInTheDocument();
  });

  it("renders only the single cover image when render_url is absent", async () => {
    renderCard({ ...MODEL, cover: "/covers/1.jpg", render_url: null });

    await screen.findByText("Articulated Dragon");
    expect(screen.queryByTestId("render-hover-img")).not.toBeInTheDocument();
  });

  it("renders only the single cover image when render_url equals cover", async () => {
    renderCard({ ...MODEL, cover: "/covers/1.jpg", render_url: "/covers/1.jpg" });

    await screen.findByText("Articulated Dragon");
    expect(screen.queryByTestId("render-hover-img")).not.toBeInTheDocument();
  });

  it("never renders a hover image when there's no cover to begin with", async () => {
    renderCard({ ...MODEL, cover: null, render_url: "/renders/1.png" });

    await screen.findByText("Articulated Dragon");
    expect(screen.queryByTestId("render-hover-img")).not.toBeInTheDocument();
  });
});

describe("ModelCard -- lazy, non-shifting thumbnails (R9-A item 1)", () => {
  it("renders the resting cover with lazy-loading attrs", async () => {
    renderCard({ ...MODEL, cover: "/covers/1.jpg" });

    const cover = await screen.findByAltText("Articulated Dragon");
    expect(cover).toHaveAttribute("loading", "lazy");
    expect(cover).toHaveAttribute("decoding", "async");
    expect(cover).toHaveAttribute("fetchPriority", "low");
  });

  it("doesn't set the hover image's src until the card has been hovered once, then keeps it mounted", async () => {
    renderCard({ ...MODEL, cover: "/covers/1.jpg", render_url: "/renders/1.png" });

    await screen.findByText("Articulated Dragon");
    const hoverImg = screen.getByTestId("render-hover-img");
    expect(hoverImg).toHaveAttribute("loading", "lazy");
    expect(hoverImg).toHaveAttribute("decoding", "async");
    expect(hoverImg).toHaveAttribute("fetchPriority", "low");
    expect(hoverImg).not.toHaveAttribute("src");

    fireEvent.pointerEnter(screen.getByRole("link"));

    expect(screen.getByTestId("render-hover-img")).toHaveAttribute("src", "/renders/1.png");
  });
});

describe("ModelCard -- prefetch on intent (R9-A item 4)", () => {
  it("prefetches the model detail query on pointerenter, exactly once", async () => {
    const { queryClient } = renderCard(MODEL);
    const link = await screen.findByRole("link");

    fireEvent.pointerEnter(link);
    fireEvent.pointerEnter(link);

    await waitFor(() => expect(getMock).toHaveBeenCalledWith(`/models/${MODEL.slug}`));
    expect(getMock.mock.calls.filter(([path]) => path === `/models/${MODEL.slug}`)).toHaveLength(1);
    expect(queryClient.getQueryData(["models", "detail", MODEL.slug])).toBeDefined();
  });

  it("prefetches the model detail query on focus too", async () => {
    renderCard(MODEL);
    const link = await screen.findByRole("link");

    fireEvent.focus(link);

    await waitFor(() => expect(getMock).toHaveBeenCalledWith(`/models/${MODEL.slug}`));
  });
});

describe("ModelCard -- ctrl/cmd/shift+click range select (R9-A item 6)", () => {
  it("calls onModifiedClick with the index and prevents navigation on a shift-click", async () => {
    const onModifiedClick = vi.fn();
    const { router } = renderCard(MODEL, { index: 3, onModifiedClick });
    const link = await screen.findByRole("link");

    fireEvent.click(link, { shiftKey: true });

    expect(onModifiedClick).toHaveBeenCalledTimes(1);
    expect(onModifiedClick.mock.calls[0][1]).toBe(3);
    expect(router.state.location.pathname).toBe("/");
  });

  it("calls onModifiedClick and prevents navigation on a ctrl-click", async () => {
    const onModifiedClick = vi.fn();
    const { router } = renderCard(MODEL, { index: 1, onModifiedClick });
    const link = await screen.findByRole("link");

    fireEvent.click(link, { ctrlKey: true });

    expect(onModifiedClick).toHaveBeenCalledTimes(1);
    expect(router.state.location.pathname).toBe("/");
  });

  it("calls onModifiedClick and prevents navigation on a cmd (meta) click", async () => {
    const onModifiedClick = vi.fn();
    const { router } = renderCard(MODEL, { index: 2, onModifiedClick });
    const link = await screen.findByRole("link");

    fireEvent.click(link, { metaKey: true });

    expect(onModifiedClick).toHaveBeenCalledTimes(1);
    expect(router.state.location.pathname).toBe("/");
  });

  it("plain clicks navigate normally and don't call onModifiedClick", async () => {
    const onModifiedClick = vi.fn();
    renderCard(MODEL, { index: 0, onModifiedClick });
    const link = await screen.findByRole("link");

    fireEvent.click(link);

    expect(onModifiedClick).not.toHaveBeenCalled();
  });
});

describe("ModelCard -- format chips (Phase 5)", () => {
  it("dedupes formats and caps chips at 3 with a +N overflow badge", async () => {
    renderCard({ ...MODEL, formats: ["stl", "stl", "3mf", "step", "obj"] });

    const formatBadges = within(await screen.findByTestId("format-badges"));
    expect(formatBadges.getByText("STL")).toBeInTheDocument();
    expect(formatBadges.getByText("3MF")).toBeInTheDocument();
    expect(formatBadges.getByText("STEP")).toBeInTheDocument();
    expect(formatBadges.queryByText("OBJ")).not.toBeInTheDocument();
    expect(formatBadges.getByText("+1")).toBeInTheDocument();
  });

  it("shows no overflow badge when there are 3 or fewer unique formats", async () => {
    renderCard({ ...MODEL, formats: ["stl", "3mf"] });

    const formatBadges = within(await screen.findByTestId("format-badges"));
    expect(formatBadges.queryByText(/^\+\d+$/)).not.toBeInTheDocument();
  });
});

describe("ModelCard -- hover overlay with dims and quick actions (Phase 5)", () => {
  const SLICER_FILE: FileOut = {
    id: 10,
    revision_id: 1,
    rel_path: "model.3mf",
    storage_path: "/x",
    blob_hash: "hash",
    size: 100,
    format: "3mf",
    kind: "cad",
    mtime: null,
    verified_at: null,
    meta: null,
    thumb_ready: false,
    glb_status: null,
    glb_preview_ready: false,
  };
  it("shows formatted mm dimensions in the hover overlay", async () => {
    renderCard({ ...MODEL, dims_mm: [120.456, 80.1, 45] });

    const overlay = within(await screen.findByTestId("hover-overlay"));
    expect(overlay.getByText("120.5 × 80.1 × 45.0 mm")).toBeInTheDocument();
  });

  it("renders no hover overlay when there are no dims and no quick actions", async () => {
    renderCard(MODEL);

    await screen.findByText("Articulated Dragon");
    expect(screen.queryByTestId("hover-overlay")).not.toBeInTheDocument();
  });

  it("shows an Open in slicer quick action when best_slicer_file is set", async () => {
    renderCard({ ...MODEL, best_slicer_file: SLICER_FILE });

    expect(await screen.findByTestId("hover-overlay")).toBeInTheDocument();
    expect(screen.getByLabelText(/Open model\.3mf in/)).toBeInTheDocument();
  });

  it("quick actions stop propagation instead of navigating the card", async () => {
    const { router } = renderCard({ ...MODEL, best_slicer_file: SLICER_FILE });

    fireEvent.click(await screen.findByLabelText(/Open model\.3mf in/));

    expect(router.state.location.pathname).toBe("/");
  });

  it("hides the printer quick action when printable_file is unset even with a slicer file present", async () => {
    renderCard({ ...MODEL, best_slicer_file: SLICER_FILE, printable_file: null });

    await screen.findByTestId("hover-overlay");
    expect(screen.queryByLabelText(/^Print /)).not.toBeInTheDocument();
  });
});
