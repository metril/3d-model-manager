import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TagPicker } from "@/components/tags/TagPicker";
import type { TagRef } from "@/api/types";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock } };
});

// Radix popovers don't open under jsdom -- render content in place.
vi.mock("@/components/ui/popover", () => ({
  Popover: ({ children }: { children?: ReactNode }) => <>{children}</>,
  PopoverTrigger: ({ children }: { children?: ReactNode }) => <>{children}</>,
  PopoverContent: ({ children }: { children?: ReactNode }) => <>{children}</>,
}));

const FANTASY: TagRef = { id: 1, name: "fantasy", color: "#14b8a6", icon: null, description: null };
const DRAGON: TagRef = { id: 2, name: "dragon", color: null, icon: "flame", description: null };

function renderPicker(props: { value: TagRef[]; onChange: (next: TagRef[]) => void }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <TagPicker {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getMock.mockReset().mockResolvedValue([FANTASY, DRAGON]);
  postMock.mockReset();
});

describe("TagPicker", () => {
  it("toggles an existing tag on and off, reporting the full new set", async () => {
    const onChange = vi.fn();
    renderPicker({ value: [FANTASY], onChange });

    const list = await screen.findByRole("list", { name: "Tags" });
    fireEvent.click(await within(list).findByRole("button", { name: /dragon/ }));
    expect(onChange).toHaveBeenLastCalledWith([FANTASY, DRAGON]);

    fireEvent.click(within(list).getByRole("button", { name: /fantasy/ }));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it("filters by search text", async () => {
    renderPicker({ value: [], onChange: vi.fn() });
    await screen.findByRole("button", { name: /dragon/ });

    fireEvent.change(screen.getByLabelText("Search tags"), { target: { value: "drag" } });

    expect(screen.queryByRole("button", { name: /fantasy/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /dragon/ })).toBeInTheDocument();
  });

  it("creates a new tag inline with color, icon and description, then selects it", async () => {
    const created: TagRef = { id: 9, name: "robots", color: "#ef4444", icon: "bot", description: "Beep boop" };
    postMock.mockResolvedValue(created);
    const onChange = vi.fn();
    renderPicker({ value: [FANTASY], onChange });
    await screen.findByRole("button", { name: /dragon/ });

    fireEvent.change(screen.getByLabelText("Search tags"), { target: { value: "robots" } });
    fireEvent.click(screen.getByRole("button", { name: /Create “robots”/ }));

    fireEvent.click(screen.getByRole("button", { name: "Color red" }));
    fireEvent.click(screen.getByRole("button", { name: "Icon bot" }));
    fireEvent.change(screen.getByLabelText("Tag description"), { target: { value: "Beep boop" } });
    fireEvent.click(screen.getByRole("button", { name: "Create tag" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/tags", {
        name: "robots",
        color: "#ef4444",
        icon: "bot",
        description: "Beep boop",
      }),
    );
    await waitFor(() => expect(onChange).toHaveBeenCalledWith([FANTASY, created]));
  });

  it("accepts a custom hex color", async () => {
    postMock.mockResolvedValue({ id: 10, name: "teal-ish", color: "#12ab34" });
    renderPicker({ value: [], onChange: vi.fn() });
    await screen.findByRole("button", { name: /dragon/ });

    fireEvent.change(screen.getByLabelText("Search tags"), { target: { value: "teal-ish" } });
    fireEvent.click(screen.getByRole("button", { name: /Create/ }));
    fireEvent.change(screen.getByLabelText("Hex color"), { target: { value: "#12AB34" } });
    fireEvent.click(screen.getByRole("button", { name: "Create tag" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/tags", expect.objectContaining({ name: "teal-ish", color: "#12ab34" })),
    );
  });

  it("does not offer to create a name that already exists", async () => {
    renderPicker({ value: [], onChange: vi.fn() });
    await screen.findByRole("button", { name: /dragon/ });

    fireEvent.change(screen.getByLabelText("Search tags"), { target: { value: "Dragon" } });

    expect(screen.queryByRole("button", { name: /Create/ })).not.toBeInTheDocument();
  });
});
