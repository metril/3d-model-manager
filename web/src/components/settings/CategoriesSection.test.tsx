import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CategoriesSection } from "@/components/settings/CategoriesSection";
import type { CategoryOut } from "@/api/types";

// Radix's Popover never reaches an interactive open state under jsdom (same
// limitation documented in LibraryPage.test.tsx) -- render trigger/content
// unconditionally so the color swatch grid is reachable without a real
// open click.
vi.mock("@/components/ui/popover", () => ({
  Popover: ({ children }: { children?: ReactNode }) => <>{children}</>,
  PopoverTrigger: ({ children }: { children?: ReactNode }) => <>{children}</>,
  PopoverContent: ({ children }: { children?: ReactNode }) => <>{children}</>,
}));

const { categoriesBox, postMock, patchMock } = vi.hoisted(() => ({
  categoriesBox: {
    current: [{ id: 1, name: "Miniatures", color: "red", model_count: 2 } as CategoryOut],
  },
  postMock: vi.fn().mockResolvedValue({ id: 2, name: "Vases", color: "teal", model_count: 0 }),
  patchMock: vi.fn().mockResolvedValue({ id: 1, name: "Miniatures", color: "teal", model_count: 2 }),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      get: vi.fn().mockImplementation(() => Promise.resolve(categoriesBox.current)),
      post: postMock,
      patch: patchMock,
    },
  };
});

function renderSection() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <CategoriesSection />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  postMock.mockClear();
  patchMock.mockClear();
});

describe("CategoriesSection -- error state", () => {
  it("shows a styled error card with Retry when the list query fails, and refetches on click", async () => {
    const getMock = vi.mocked((await import("@/api/client")).api.get);
    getMock.mockRejectedValueOnce(new Error("boom"));
    renderSection();

    expect(await screen.findByText("Couldn't load categories")).toBeInTheDocument();
    expect(screen.queryByText("not found")).not.toBeInTheDocument();

    getMock.mockResolvedValueOnce(categoriesBox.current);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("Miniatures")).toBeInTheDocument();
  });
});

describe("CategoriesSection -- color palette (fix-review finding 1/2)", () => {
  it("sends a TagColor palette name (not hex) when creating a category", async () => {
    renderSection();

    fireEvent.click(await screen.findByRole("button", { name: "Add category" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Vases" } });
    // Palette swatch grid, same pattern as TagEditor's TagColorPicker: open
    // the picker, then pick a named color.
    fireEvent.click(screen.getByRole("button", { name: "Category color" }));
    fireEvent.click(screen.getByRole("button", { name: "Color teal" }));
    // The trigger button behind the (modal) dialog is `aria-hidden` while
    // it's open, so only the dialog's own submit button matches this role
    // query now.
    fireEvent.click(screen.getByRole("button", { name: "Add category" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/categories", { name: "Vases", color: "teal" }),
    );
  });

  it("reopens Add clean after a successful create", async () => {
    renderSection();

    fireEvent.click(await screen.findByRole("button", { name: "Add category" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Vases" } });
    fireEvent.click(screen.getByRole("button", { name: "Add category" }));
    await waitFor(() => expect(screen.queryByLabelText("Name")).toBeNull());

    fireEvent.click(await screen.findByRole("button", { name: "Add category" }));
    expect(screen.getByLabelText("Name")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByText("Discard changes?")).toBeNull();
  });

  it("sends a TagColor palette name (not hex) when editing a category's color", async () => {
    renderSection();

    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("button", { name: "Category color" }));
    fireEvent.click(screen.getByRole("button", { name: "Color teal" }));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledWith("/categories/1", { name: "Miniatures", color: "teal" }),
    );
  });

  it("renders the category's color swatch via tagColorClass, not an inline background style", async () => {
    renderSection();

    const dot = (await screen.findByText("Miniatures")).closest("tr")?.querySelector("span[aria-hidden]");
    expect(dot).not.toBeNull();
    expect(dot).toHaveClass("bg-red-100");
    expect(dot?.getAttribute("style") ?? "").not.toContain("background-color");
  });
});
