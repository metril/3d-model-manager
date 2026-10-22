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

import { ModelHeader } from "@/components/model-detail/ModelHeader";
import type { ModelDetail } from "@/api/types";

vi.mock("@/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/auth")>()),
  useAuth: () => ({ data: { id: 1, username: "tester" }, can: () => true, canPrinter: () => true }),
}));


const { getMock, patchMock, deleteMock, postMock } = vi.hoisted(() => ({
  getMock: vi.fn().mockResolvedValue([]),
  patchMock: vi.fn().mockResolvedValue({}),
  deleteMock: vi.fn().mockResolvedValue(undefined),
  postMock: vi.fn().mockResolvedValue({}),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock, patch: patchMock, delete: deleteMock, post: postMock },
  };
});

const MODEL: ModelDetail = {
  id: 1,
  slug: "articulated-dragon",
  name: "Articulated Dragon",
  description: "A flexible print-in-place dragon",
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
  tags: ["fantasy", "dragon"],
  current_revision: null,
  notes: [],
  backends: [],
  favorite: false,
  print_count: 0,
  last_printed_at: null,
  metadata: null,
  print_tips: null,
};

const MODEL_WITH_SOURCE: ModelDetail = {
  ...MODEL,
  source_url: "https://www.thingiverse.com/thing:123",
  source_site: "thingiverse",
};

function renderHeader(
  editMode: boolean,
  onToggleEditMode = vi.fn(),
  model: ModelDetail = MODEL,
  onOpenRelocate = vi.fn(),
) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const rootRoute = createRootRoute();
  // Starts on a dedicated "/detail" route (rather than "/library") so a test
  // can assert the Delete action's "navigate to library" behavior by
  // observing `router.state.location.pathname` actually change to
  // "/library".
  const homeRoute = createRoute({ getParentRoute: () => rootRoute, path: "/library", component: () => null });
  const detailRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/detail",
    component: () => (
      <ModelHeader
        model={model}
        editMode={editMode}
        onToggleEditMode={onToggleEditMode}
        onOpenRelocate={onOpenRelocate}
      />
    ),
  });
  const jobsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/jobs", component: () => null });
  const router = createRouter({
    routeTree: rootRoute.addChildren([homeRoute, detailRoute, jobsRoute]),
    history: createMemoryHistory({ initialEntries: ["/detail"] }),
  });
  return {
    router,
    onToggleEditMode,
    onOpenRelocate,
    ...render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    ),
  };
}

beforeEach(() => {
  getMock.mockClear();
  patchMock.mockClear();
  deleteMock.mockClear();
  postMock.mockClear();
});

describe("ModelHeader -- read-only by default", () => {
  it("renders the name as plain text, no editing affordances", async () => {
    renderHeader(false);

    const heading = await screen.findByRole("heading", { name: "Articulated Dragon" });
    expect(heading).toBeInTheDocument();

    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit name/ })).not.toBeInTheDocument();
    // The name isn't a clickable button anymore.
    expect(screen.queryByRole("button", { name: "Articulated Dragon" })).not.toBeInTheDocument();
  });

  it("Archive/Delete are never direct buttons (they live in the overflow menu)", async () => {
    renderHeader(false);

    await screen.findByRole("heading", { name: "Articulated Dragon" });
    expect(screen.queryByRole("button", { name: "Archive" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
  });

  it("shows an outline Edit toggle button", async () => {
    renderHeader(false);

    const toggle = await screen.findByRole("button", { name: "Edit" });
    expect(toggle).toBeInTheDocument();
  });

  it("shows the favorite star, and an Add to queue action, even in read-only mode", async () => {
    renderHeader(false);

    expect(await screen.findByRole("button", { name: "Add to favorites" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add to queue" })).toBeInTheDocument();
  });
});

describe("ModelHeader -- edit mode", () => {
  it("toggling Edit calls onToggleEditMode", async () => {
    const { onToggleEditMode } = renderHeader(false);

    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));

    expect(onToggleEditMode).toHaveBeenCalledOnce();
  });

  it("reveals editing affordances for the name; toggle reads Done -- Archive/Delete stay in the overflow menu, not edit-gated", async () => {
    renderHeader(true);

    expect(await screen.findByRole("button", { name: "Done" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit name" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Archive" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
  });

  it("keeps the name as a level-1 heading in edit mode", async () => {
    renderHeader(true);

    // The heading's accessible name also picks up the embedded pencil
    // button's label, so match on the model name rather than exactly.
    const heading = await screen.findByRole("heading", { level: 1, name: /Articulated Dragon/ });
    expect(heading).toBeInTheDocument();
  });

  it("editing the name commits through InlineEdit's explicit Save", async () => {
    renderHeader(true);

    fireEvent.click(await screen.findByRole("button", { name: "Edit name" }));
    const input = screen.getByRole("textbox", { name: "name" });
    fireEvent.change(input, { target: { value: "New Name" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledExactlyOnceWith("/models/articulated-dragon", { name: "New Name" }),
    );
  });

  it("still shows the favorite star in edit mode -- it isn't gated by the edit toggle", async () => {
    renderHeader(true);

    expect(await screen.findByRole("button", { name: "Add to favorites" })).toBeInTheDocument();
  });
});

describe("ModelHeader -- favorite + queue actions", () => {
  it("toggling the favorite star PATCHes favorite:true, without touching the edit gate", async () => {
    renderHeader(false);

    fireEvent.click(await screen.findByRole("button", { name: "Add to favorites" }));

    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledExactlyOnceWith("/models/articulated-dragon", { favorite: true }),
    );
  });

  // R9-C item 5: the `f` hotkey reuses the same `usePatchModel` mutation as
  // the star button above.
  it("`f` toggles favorite via the same mutation as the star button", async () => {
    renderHeader(false);
    await screen.findByRole("button", { name: "Add to favorites" });

    fireEvent.keyDown(document.body, { key: "f" });

    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledExactlyOnceWith("/models/articulated-dragon", { favorite: true }),
    );
  });

  it("'Add to queue' posts the model id to the queue endpoint", async () => {
    renderHeader(false);

    fireEvent.click(await screen.findByRole("button", { name: "Add to queue" }));

    await waitFor(() => expect(postMock).toHaveBeenCalledExactlyOnceWith("/queue", { model_id: 1 }));
  });
});

describe("ModelHeader -- Printed N× chip (Branch 5 Task 2)", () => {
  it("shows a 'Printed N×' chip once the model has logged prints", async () => {
    renderHeader(false, vi.fn(), {
      ...MODEL,
      print_count: 3,
      last_printed_at: "2026-07-01T10:00:00Z",
    });

    expect(await screen.findByText("Printed 3×")).toBeInTheDocument();
  });

  it("hides the chip for a model with no logged prints", async () => {
    renderHeader(false);

    await screen.findByRole("heading", { name: "Articulated Dragon" });
    expect(screen.queryByText(/^Printed \d+×$/)).not.toBeInTheDocument();
  });
});

describe("ModelHeader -- badges (R13a slim header)", () => {
  it("shows an Archived badge for an archived model", async () => {
    renderHeader(false, vi.fn(), { ...MODEL, is_archived: true });

    expect(await screen.findByText("Archived")).toBeInTheDocument();
  });

  it("hides the Archived badge for a non-archived model", async () => {
    renderHeader(false);

    await screen.findByRole("heading", { name: "Articulated Dragon" });
    expect(screen.queryByText("Archived")).not.toBeInTheDocument();
  });

  it("shows a dismissible 'Needs review' badge when review_state is 'adopted'", async () => {
    renderHeader(false, vi.fn(), { ...MODEL, review_state: "adopted" });

    expect(await screen.findByTestId("review-badge")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Dismiss needs review" }));
    fireEvent.click(await screen.findByRole("button", { name: "Clear" }));

    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledExactlyOnceWith("/models/articulated-dragon", { review_state: null }),
    );
  });

  it("hides the review badge otherwise", async () => {
    renderHeader(false);

    await screen.findByRole("heading", { name: "Articulated Dragon" });
    expect(screen.queryByTestId("review-badge")).not.toBeInTheDocument();
  });
});

/** Opens the header's "More actions" overflow menu (Radix `DropdownMenu`)
 * and returns the menu element -- Archive, Delete, Re-download, and
 * Move/Copy all live behind it now, reachable in or out of edit mode.
 * `DropdownMenuTrigger` opens on `pointerdown` (not `click`, which it
 * `preventDefault`s away to avoid a double-toggle from the synthesized
 * click a real pointerdown+pointerup pair would also produce), so a plain
 * `fireEvent.click` never opens it under jsdom. */
async function openMoreActions() {
  fireEvent.pointerDown(await screen.findByRole("button", { name: "More actions" }), { button: 0 });
  return screen.findByRole("menu");
}

describe("ModelHeader -- More actions overflow menu", () => {
  it("opens from 'More actions' and lists all five items in order, with a separator before Archive", async () => {
    renderHeader(false, vi.fn(), MODEL_WITH_SOURCE);

    const menu = await openMoreActions();

    const nodes = Array.from(menu.querySelectorAll('[role="menuitem"], [role="separator"]'));
    expect(nodes.map((node) => node.getAttribute("role"))).toEqual([
      "menuitem",
      "menuitem",
      "menuitem",
      "separator",
      "menuitem",
      "menuitem",
    ]);
    expect(nodes.map((node) => node.textContent)).toEqual([
      "Re-download…",
      "Download ZIP",
      "Move / copy…",
      "",
      "Archive…",
      "Delete…",
    ]);
  });

  it("'Download ZIP' links straight at the model's zip endpoint with `download`", async () => {
    renderHeader(false);

    const menu = await openMoreActions();

    const link = within(menu).getByRole("menuitem", { name: "Download ZIP" });
    expect(link.tagName).toBe("A");
    expect(link).toHaveAttribute("href", "/api/models/articulated-dragon/zip");
    expect(link).toHaveAttribute("download");
  });

  it("styles the Delete item destructive", async () => {
    renderHeader(false);

    const menu = await openMoreActions();

    expect(within(menu).getByRole("menuitem", { name: "Delete…" })).toHaveAttribute(
      "data-variant",
      "destructive",
    );
  });

  it("'Move / copy…' calls onOpenRelocate -- the dialog itself now lives in TagsLinksCard", async () => {
    const { onOpenRelocate } = renderHeader(false);

    await openMoreActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Move / copy…" }));

    expect(onOpenRelocate).toHaveBeenCalledOnce();
  });
});

describe("ModelHeader -- archive & delete via the overflow menu (feat/import-fidelity T3/T4)", () => {
  it("never renders Archive/Delete as direct buttons outside edit mode", async () => {
    renderHeader(false);
    await screen.findByRole("heading", { name: "Articulated Dragon" });
    expect(screen.queryByRole("button", { name: "Archive" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
  });

  it("never renders Archive/Delete as direct buttons in edit mode either", async () => {
    renderHeader(true);
    await screen.findByRole("heading", { name: "Articulated Dragon" });
    expect(screen.queryByRole("button", { name: "Archive" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
  });

  it("archiving works without edit mode: confirm dialog still gates the PATCH (feat/import-fidelity T3: archive is reversible, no longer a DELETE)", async () => {
    renderHeader(false);

    await openMoreActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Archive…" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText('Archive "Articulated Dragon"?')).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Archive" }));

    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledExactlyOnceWith("/models/articulated-dragon", { is_archived: true }),
    );
    expect(deleteMock).not.toHaveBeenCalled();
  });

  it("deleting works without edit mode: requires confirmation, calls DELETE, and navigates home on success", async () => {
    const { router } = renderHeader(false);

    await openMoreActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Delete…" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Delete this model?")).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "Permanently deletes the model and every file from storage. This cannot be undone.",
      ),
    ).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(deleteMock).toHaveBeenCalledExactlyOnceWith("/models/articulated-dragon"));
    await waitFor(() => expect(router.state.location.pathname).toBe("/library"));
  });
});

describe("ModelHeader -- re-download (feat/import-fidelity T4)", () => {
  it("disables the Re-download menu item when the model has no import source", async () => {
    renderHeader(false);

    const menu = await openMoreActions();

    expect(within(menu).getByRole("menuitem", { name: "Re-download…" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it("enables Re-download for a model with a source, even outside edit mode", async () => {
    renderHeader(false, vi.fn(), MODEL_WITH_SOURCE);

    const menu = await openMoreActions();

    expect(within(menu).getByRole("menuitem", { name: "Re-download…" })).not.toHaveAttribute("aria-disabled");
  });

  it("defaults to 'New revision' and POSTs {mode: 'revision'} on Start", async () => {
    renderHeader(false, vi.fn(), MODEL_WITH_SOURCE);

    await openMoreActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Re-download…" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("radio", { name: /New revision/ })).toHaveAttribute("aria-checked", "true");

    fireEvent.click(within(dialog).getByRole("button", { name: "Start" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledExactlyOnceWith("/models/articulated-dragon/redownload", {
        mode: "revision",
      }),
    );
  });

  it("switching to 'Replace current files' POSTs {mode: 'replace'}", async () => {
    renderHeader(false, vi.fn(), MODEL_WITH_SOURCE);

    await openMoreActions();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Re-download…" }));

    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("radio", { name: "Replace current files" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Start" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledExactlyOnceWith("/models/articulated-dragon/redownload", {
        mode: "replace",
      }),
    );
  });
});

describe("ModelHeader -- Print… (P5 Task 11)", () => {
  const SLICED_FILE = {
    id: 99,
    revision_id: 1,
    rel_path: "print.gcode.3mf",
    storage_path: "/data/print.gcode.3mf",
    blob_hash: "slicedhash",
    size: 4096,
    format: "gcode_3mf" as const,
    kind: "sliced" as const,
    mtime: null,
    verified_at: null,
    meta: null,
    thumb_ready: false,
    glb_status: null,
    glb_preview_ready: false,
  };
  const MODEL_WITH_SLICED_FILE: ModelDetail = {
    ...MODEL,
    current_revision: {
      id: 1,
      model_id: 1,
      number: 1,
      name: null,
      note: null,
      dir_name: "rev-1",
      created_at: "2026-06-01T12:00:00Z",
      files: [SLICED_FILE],
      notes: [],
    },
  };

  it("hides Print… when the model has no sliced file", async () => {
    renderHeader(false, vi.fn(), MODEL);
    await screen.findByText("Articulated Dragon");
    expect(screen.queryByRole("button", { name: "Print…" })).not.toBeInTheDocument();
  });

  it("shows Print… and opens the shared PrintDialog for a sliced model", async () => {
    renderHeader(false, vi.fn(), MODEL_WITH_SLICED_FILE);

    fireEvent.click(await screen.findByRole("button", { name: "Print…" }));

    expect(await screen.findByText("Print")).toBeInTheDocument();
  });
});
