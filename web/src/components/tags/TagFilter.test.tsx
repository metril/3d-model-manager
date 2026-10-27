import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { useState, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { appendTagParams } from "@/api/tags";
import { TagFilter } from "@/components/tags/TagFilter";
import { EMPTY_TAG_FILTER, matchesTagFilter, type TagFilterValue } from "@/components/tags/tagFilter";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock } };
});

vi.mock("@/components/ui/popover", () => ({
  Popover: ({ children }: { children?: ReactNode }) => <>{children}</>,
  PopoverTrigger: ({ children }: { children?: ReactNode }) => <>{children}</>,
  PopoverContent: ({ children }: { children?: ReactNode }) => <>{children}</>,
}));

const counts = (models: number) => ({ models, model_files: 0, projects: 0, attachments: 0, archives: 0, collections: 0, total: models });

beforeEach(() => {
  getMock.mockReset().mockResolvedValue([
    { id: 1, name: "fantasy", color: "#14b8a6", counts: counts(4) },
    { id: 2, name: "dragon", color: null, counts: counts(2) },
  ]);
});

function Harness({ onValue }: { onValue: (value: TagFilterValue) => void }) {
  const [value, setValue] = useState<TagFilterValue>(EMPTY_TAG_FILTER);
  return (
    <TagFilter
      value={value}
      countKey="models"
      onChange={(next) => {
        setValue(next);
        onValue(next);
      }}
    />
  );
}

function renderFilter(onValue: (value: TagFilterValue) => void) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <Harness onValue={onValue} />
    </QueryClientProvider>,
  );
}

describe("TagFilter", () => {
  it("shows usage counts and hides the any/all toggle until two tags are picked", async () => {
    renderFilter(vi.fn());
    expect(await screen.findByText("4")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Match mode" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /fantasy/ }));
    expect(screen.queryByRole("group", { name: "Match mode" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /dragon/ }));
    expect(screen.getByRole("group", { name: "Match mode" })).toBeInTheDocument();
  });

  it("reports selected ids, switches any/all, and clears", async () => {
    const onValue = vi.fn();
    renderFilter(onValue);

    fireEvent.click(await screen.findByRole("button", { name: /fantasy/ }));
    fireEvent.click(screen.getByRole("button", { name: /dragon/ }));
    expect(onValue).toHaveBeenLastCalledWith({ tags: [1, 2], mode: "any" });

    fireEvent.click(screen.getByRole("button", { name: "All" }));
    expect(onValue).toHaveBeenLastCalledWith({ tags: [1, 2], mode: "all" });

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(onValue).toHaveBeenLastCalledWith(EMPTY_TAG_FILTER);
  });
});

describe("tag filter params", () => {
  it("emits repeated tags= params and tag_mode only for all", () => {
    const any = new URLSearchParams();
    appendTagParams(any, [1, 2], "any");
    expect(any.toString()).toBe("tags=1&tags=2");

    const all = new URLSearchParams();
    appendTagParams(all, [1, 2], "all");
    expect(all.toString()).toBe("tags=1&tags=2&tag_mode=all");

    const none = new URLSearchParams();
    appendTagParams(none, [], "all");
    expect(none.toString()).toBe("");
  });

  it("matchesTagFilter honours any vs all", () => {
    const refs = [{ id: 1, name: "a" }, { id: 3, name: "c" }];
    expect(matchesTagFilter(refs, { tags: [1, 2], mode: "any" })).toBe(true);
    expect(matchesTagFilter(refs, { tags: [1, 2], mode: "all" })).toBe(false);
    expect(matchesTagFilter(refs, { tags: [1, 3], mode: "all" })).toBe(true);
    expect(matchesTagFilter(undefined, EMPTY_TAG_FILTER)).toBe(true);
  });
});
