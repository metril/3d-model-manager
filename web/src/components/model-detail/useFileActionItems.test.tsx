import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FileActionsMenu } from "@/components/model-detail/fileActionsHarness.test-util";
import type { FileOut, ModelDetail } from "@/api/types";

const { deleteMock, patchMock } = vi.hoisted(() => ({
  deleteMock: vi.fn().mockResolvedValue(undefined),
  patchMock: vi.fn().mockResolvedValue({}),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, delete: deleteMock, patch: patchMock } };
});

vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

function buildFile(overrides: Partial<FileOut>): FileOut {
  return {
    id: 1,
    revision_id: 1,
    rel_path: "model.stl",
    storage_path: "/data/model.stl",
    blob_hash: "hash1",
    size: 2048,
    format: "stl",
    kind: "mesh",
    mtime: "2026-06-01T12:00:00Z",
    verified_at: "2026-06-01T12:00:05Z",
    meta: null,
    thumb_ready: false,
    glb_status: null,
    glb_preview_ready: false,
    ...overrides,
  };
}

function buildModel(): ModelDetail {
  return {
    id: 1,
    slug: "test-model",
    name: "Test Model",
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
    notes: [],
    backends: [],
    favorite: false,
    print_count: 0,
    last_printed_at: null,
    current_revision: null,
  } as unknown as ModelDetail;
}

function openMenu(file: FileOut) {
  fireEvent.pointerDown(screen.getByRole("button", { name: `More actions for ${file.rel_path}` }), { button: 0 });
}

function renderMenu(file: FileOut, extra: Partial<Parameters<typeof FileActionsMenu>[0]> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <FileActionsMenu file={file} model={buildModel()} {...extra} />
    </QueryClientProvider>,
  );
}

describe("FileActionsMenu", () => {
  beforeEach(() => {
    deleteMock.mockReset().mockResolvedValue(undefined);
    patchMock.mockReset().mockResolvedValue({});
  });

  it("shows a working Download link for a verified file", async () => {
    const file = buildFile({ verified_at: "2026-06-01T12:00:05Z" });
    renderMenu(file);
    openMenu(file);

    const downloadLink = await screen.findByRole("menuitem", { name: `Download ${file.rel_path}` });
    expect(downloadLink).toHaveAttribute("href", `/api/files/${file.id}/download`);
  });

  it("disables Download for a file still processing", async () => {
    const file = buildFile({ verified_at: null });
    renderMenu(file);
    openMenu(file);

    const downloadItem = await screen.findByRole("menuitem", { name: `Download ${file.rel_path}` });
    expect(downloadItem).toHaveAttribute("aria-disabled", "true");
  });

  it("deletes the file after confirming", async () => {
    const file = buildFile({ id: 42 });
    renderMenu(file);
    openMenu(file);

    fireEvent.click(await screen.findByRole("menuitem", { name: `Delete ${file.rel_path}` }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith(`/files/${file.id}`));
  });

  it("patches cover_blob_hash when 'Set as cover' is chosen for an image file", async () => {
    const file = buildFile({ id: 7, kind: "image", format: "png", blob_hash: "imghash" });
    renderMenu(file);
    openMenu(file);

    fireEvent.click(await screen.findByRole("menuitem", { name: "Set as cover" }));

    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledWith("/models/test-model", { cover_blob_hash: "imghash" }),
    );
  });

  it("patches cover_blob_hash when 'Set as preview' is chosen for a glb-ready mesh file", async () => {
    const file = buildFile({ id: 8, kind: "mesh", glb_status: "ok", blob_hash: "meshhash" });
    renderMenu(file);
    openMenu(file);

    fireEvent.click(await screen.findByRole("menuitem", { name: "Set as preview" }));

    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledWith("/models/test-model", { cover_blob_hash: "meshhash" }),
    );
  });

  it("does not offer Set as cover/preview for a non-image, non-glb-ready file", async () => {
    const file = buildFile({ kind: "mesh", glb_status: "pending" });
    renderMenu(file);
    openMenu(file);

    await screen.findByRole("menuitem", { name: `Download ${file.rel_path}` });
    expect(screen.queryByRole("menuitem", { name: "Set as cover" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Set as preview" })).not.toBeInTheDocument();
  });

  it("fires onViewIn3D for a studio-viewable file when provided", async () => {
    const file = buildFile({ glb_status: "ok" });
    const onViewIn3D = vi.fn();
    renderMenu(file, { onViewIn3D });
    openMenu(file);

    fireEvent.click(await screen.findByRole("menuitem", { name: `View ${file.rel_path} in 3D` }));
    expect(onViewIn3D).toHaveBeenCalledWith(file);
  });

  it("omits View in 3D when isDoc is set, even for a studio-viewable format", async () => {
    const file = buildFile({ glb_status: "ok" });
    const onViewIn3D = vi.fn();
    renderMenu(file, { onViewIn3D, isDoc: true });
    openMenu(file);

    await screen.findByRole("menuitem", { name: `Download ${file.rel_path}` });
    expect(screen.queryByRole("menuitem", { name: `View ${file.rel_path} in 3D` })).not.toBeInTheDocument();
  });
});
