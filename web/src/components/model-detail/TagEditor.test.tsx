import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TagEditor } from "@/components/model-detail/TagEditor";
import type { ModelDetail } from "@/api/types";

const { getMock, putMock } = vi.hoisted(() => ({
  getMock: vi.fn().mockResolvedValue([]),
  putMock: vi.fn().mockResolvedValue([]),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock, put: putMock },
  };
});

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
  tags: ["fantasy", "dragon"],
  tag_refs: [
    { id: 1, name: "fantasy", color: "#14b8a6" },
    { id: 2, name: "dragon", color: null },
  ],
  current_revision: null,
  notes: [],
  backends: [],
  favorite: false,
  print_count: 0,
  last_printed_at: null,
  metadata: null,
  print_tips: null,
};

function renderEditor(editMode: boolean) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <TagEditor model={MODEL} editMode={editMode} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getMock.mockClear();
  putMock.mockClear();
});

describe("TagEditor", () => {
  it("shows tag chips without remove buttons or the picker by default", () => {
    renderEditor(false);

    expect(screen.getByText("fantasy")).toBeInTheDocument();
    expect(screen.getByText("dragon")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Remove tag/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit tags" })).not.toBeInTheDocument();
  });

  it("shows remove buttons and the picker in edit mode", () => {
    renderEditor(true);

    expect(screen.getByRole("button", { name: "Remove tag fantasy" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove tag dragon" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit tags" })).toBeInTheDocument();
  });

  it("removing a chip PUTs the remaining tag ids", async () => {
    renderEditor(true);

    fireEvent.click(screen.getByRole("button", { name: "Remove tag fantasy" }));

    await waitFor(() => expect(putMock).toHaveBeenCalledWith("/models/1/tags", { tag_ids: [2] }));
  });
});
