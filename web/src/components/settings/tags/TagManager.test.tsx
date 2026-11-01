import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { TagOut, TagUsage } from "@/api/types";
import { TagManager } from "@/components/settings/tags/TagManager";
import { filterAndSort } from "@/components/settings/tags/tagSort";

// Radix Popover never reaches an interactive open state under jsdom (see CategoriesSection.test).
vi.mock("@/components/ui/popover", () => ({
  Popover: ({ children }: { children?: ReactNode }) => <>{children}</>,
  PopoverTrigger: ({ children }: { children?: ReactNode }) => <>{children}</>,
  PopoverContent: ({ children }: { children?: ReactNode }) => <>{children}</>,
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to, className }: { children?: ReactNode; to: string; className?: string }) => (
    <a href={to} className={className}>
      {children}
    </a>
  ),
}));

const counts = (o: Partial<TagOut["counts"] & object> = {}) => ({
  models: 0,
  model_files: 0,
  projects: 0,
  attachments: 0,
  archives: 0,
  collections: 0,
  total: 0,
  ...o,
});

const TAGS: TagOut[] = [
  { id: 1, name: "Alpha", color: null, icon: null, description: "first one", counts: counts({ models: 3, total: 3 }) },
  { id: 2, name: "Bravo", color: null, icon: null, description: null, counts: counts() },
  { id: 3, name: "Charlie", color: null, icon: null, description: null, counts: counts({ projects: 5, total: 5 }) },
  { id: 4, name: "Delta", color: null, icon: null, description: "unused extra", counts: counts() },
];

const USAGE: TagUsage = {
  counts: counts({ models: 1, total: 1 }),
  models: [{ id: 9, slug: "vase", name: "Spiral Vase", thumbnail_url: "/api/blobs/x/thumb?size=256" }],
  projects: [],
  collections: [],
  archives: [],
};

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock } };
});

beforeAll(() => {
  // jsdom has no layout: give the scroll container a size so react-virtual renders rows.
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, value: 600 });
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, value: 1000 });
});

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  getMock.mockImplementation((url: string) =>
    Promise.resolve(url.includes("/usage") ? USAGE : TAGS),
  );
  postMock.mockResolvedValue({ results: [], not_found: [] });
});

function renderManager() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <TagManager />
    </QueryClientProvider>,
  );
}

const names = () => screen.getAllByTestId("tag-row").map((r) => within(r).getAllByTestId("tag-chip")[0].textContent);

describe("TagManager list", () => {
  it("filters by search (name + description), unused-only, and shows N of M", async () => {
    renderManager();
    expect(await screen.findByTestId("tag-count")).toHaveTextContent("4 of 4");

    fireEvent.change(screen.getByPlaceholderText("Search name or description…"), { target: { value: "extra" } });
    expect(screen.getByTestId("tag-count")).toHaveTextContent("1 of 4");
    expect(names()).toEqual(["Delta"]);

    fireEvent.change(screen.getByPlaceholderText("Search name or description…"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Unused only" }));
    expect(names()).toEqual(["Bravo", "Delta"]);
    expect(screen.getByTestId("tag-count")).toHaveTextContent("2 of 4");
  });

  it("sorts by clickable headers, toggling direction", async () => {
    renderManager();
    await screen.findByTestId("tag-count");
    expect(names()).toEqual(["Alpha", "Bravo", "Charlie", "Delta"]);

    fireEvent.click(screen.getByRole("button", { name: "Total" })); // numeric: desc first
    expect(names()).toEqual(["Charlie", "Alpha", "Bravo", "Delta"]);
    fireEvent.click(screen.getByRole("button", { name: "Total" }));
    expect(names()).toEqual(["Bravo", "Delta", "Alpha", "Charlie"]);

    fireEvent.click(screen.getByRole("button", { name: "Tag" }));
    fireEvent.click(screen.getByRole("button", { name: "Tag" }));
    expect(names()).toEqual(["Delta", "Charlie", "Bravo", "Alpha"]);
  });

  it("filterAndSort sorts by a per-entity count", () => {
    const out = filterAndSort(TAGS, { query: "", unusedOnly: false, sort: "projects", dir: "desc" });
    expect(out[0].name).toBe("Charlie");
  });
});

describe("TagManager selection + bulk actions", () => {
  it("selects rows, select-all-visible, shift-range, and clears", async () => {
    renderManager();
    await screen.findByTestId("tag-count");
    expect(screen.queryByRole("toolbar")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", { name: "Select Alpha" }));
    expect(screen.getByRole("toolbar")).toHaveTextContent("1 selected");

    fireEvent.click(screen.getByRole("checkbox", { name: "Select Charlie" }), { shiftKey: true });
    expect(screen.getByRole("toolbar")).toHaveTextContent("3 selected");

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.queryByRole("toolbar")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", { name: "Select all visible tags" }));
    expect(screen.getByRole("toolbar")).toHaveTextContent("4 selected");
  });

  it("bulk merge: pick target, confirm with count, posts bulk-merge and clears selection", async () => {
    renderManager();
    await screen.findByTestId("tag-count");
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Alpha" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Bravo" }));

    const bar = screen.getByRole("toolbar");
    expect(within(bar).getByRole("button", { name: "Merge…" })).toBeInTheDocument();
    // Popover is mocked open: the bar's picker lists every tag; choose Charlie.
    fireEvent.click(within(bar).getByRole("button", { name: /Charlie/ }));

    expect(await screen.findByText('Merge 2 tags into "Charlie"?')).toBeInTheDocument();
    postMock.mockResolvedValueOnce({ results: [{ id: 1, ok: true }, { id: 2, ok: true }], not_found: [] });
    fireEvent.click(screen.getByRole("button", { name: "Merge" }));

    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/tags/bulk-merge", { ids: [1, 2], into_id: 3 }));
    await waitFor(() => expect(screen.queryByRole("toolbar")).not.toBeInTheDocument());
  });

  it("bulk merge: target as the only selected tag disables confirm", async () => {
    renderManager();
    await screen.findByTestId("tag-count");
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Charlie" }));
    const bar = screen.getByRole("toolbar");
    fireEvent.click(within(bar).getByRole("button", { name: /Charlie/ }));
    const confirm = await screen.findByRole("button", { name: "Merge" });
    expect(confirm).toBeDisabled();
  });

  it("bulk delete: confirms with total usage and posts bulk-delete", async () => {
    renderManager();
    await screen.findByTestId("tag-count");
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Alpha" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Charlie" }));

    fireEvent.click(within(screen.getByRole("toolbar")).getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("Delete 2 tags?")).toBeInTheDocument();
    expect(screen.getByText(/8 tagged items in total/)).toBeInTheDocument();

    postMock.mockResolvedValueOnce({ results: [{ id: 1, ok: true }, { id: 3, ok: true }], not_found: [] });
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/tags/bulk-delete", { ids: [1, 3] }));
  });
});

describe("TagManager usage sheet", () => {
  it("opens from the tag name, lists models and links to the library", async () => {
    renderManager();
    await screen.findByTestId("tag-count");
    fireEvent.click(screen.getByRole("button", { name: "View usage of Alpha" }));

    expect(await screen.findByText("Spiral Vase")).toBeInTheDocument();
    expect(getMock).toHaveBeenCalledWith("/tags/1/usage?limit=50");
    expect(screen.getByRole("link", { name: "View in Library" })).toHaveAttribute("href", "/library");
  });
});
