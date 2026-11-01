import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FileExplorer } from "@/components/model-detail/explorer/FileExplorer";
import type { FileOut, ModelDetail } from "@/api/types";

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }));

vi.mock("@/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/auth")>()),
  useAuth: () => ({
    data: { id: 1, username: "tester" },
    can: () => true,
    canPrinter: () => true,
  }),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: vi.fn().mockResolvedValue(undefined) } };
});

vi.mock("@/hooks/eventsContext", () => ({
  useEvents: () => ({ subscribe: () => () => {} }),
}));

function buildFile(overrides: Partial<FileOut>): FileOut {
  return {
    id: 1,
    revision_id: 1,
    rel_path: "manual.pdf",
    storage_path: "/data/manual.pdf",
    blob_hash: "hash1",
    size: 2048,
    format: "pdf",
    kind: "doc",
    mtime: "2026-06-01T12:00:00Z",
    verified_at: "2026-06-01T12:00:05Z",
    meta: null,
    thumb_ready: false,
    glb_status: null,
    glb_preview_ready: false,
    ...overrides,
  };
}

function buildModel(files: FileOut[]): ModelDetail {
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
    current_revision: {
      id: 1,
      model_id: 1,
      number: 1,
      name: null,
      note: null,
      dir_name: "r1",
      created_at: "2026-06-01T12:00:00Z",
      files,
      notes: [],
    },
  } as unknown as ModelDetail;
}

function renderExplorer(files: FileOut[]) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <FileExplorer model={buildModel(files)} />
    </QueryClientProvider>,
  );
}

describe("FileExplorer", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  it("shows the empty state when the revision has no files", () => {
    renderExplorer([]);
    expect(screen.getByText("No files on the current revision yet.")).toBeInTheDocument();
  });

  it("offers no Preview button for non-doc files", () => {
    const meshFile = buildFile({ id: 2, rel_path: "part.stl", format: "stl", kind: "mesh" });
    renderExplorer([meshFile]);
    expect(screen.queryByRole("button", { name: "Preview" })).not.toBeInTheDocument();
  });

  it("renders an inline iframe pointing at the ?inline=1 download URL for a pdf, once previewed", () => {
    const pdfFile = buildFile({ id: 3, rel_path: "manual.pdf", format: "pdf" });
    renderExplorer([pdfFile]);

    fireEvent.click(screen.getByRole("button", { name: "Preview" }));

    const region = screen.getByTestId(`doc-preview-${pdfFile.id}`);
    const iframe = region.querySelector("iframe");
    expect(iframe).toHaveAttribute("src", `/api/files/${pdfFile.id}/download?inline=1`);
  });

  it("fetches and renders markdown content as HTML for an md file", async () => {
    fetchMock.mockResolvedValue({ ok: true, text: () => Promise.resolve("# Hello\n\nWorld") });
    const mdFile = buildFile({ id: 4, rel_path: "readme.md", format: "md" });
    renderExplorer([mdFile]);

    fireEvent.click(screen.getByRole("button", { name: "Preview" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(`/api/files/${mdFile.id}/download?inline=1`, { credentials: "include" }),
    );

    const region = await screen.findByTestId(`doc-preview-${mdFile.id}`);
    await waitFor(() => expect(region.querySelector("h1")).toHaveTextContent("Hello"));
  });

  it("fetches and renders raw text in a <pre> for a txt file", async () => {
    fetchMock.mockResolvedValue({ ok: true, text: () => Promise.resolve("plain content here") });
    const txtFile = buildFile({ id: 5, rel_path: "notes.txt", format: "txt" });
    renderExplorer([txtFile]);

    fireEvent.click(screen.getByRole("button", { name: "Preview" }));

    const region = await screen.findByTestId(`doc-preview-${txtFile.id}`);
    await waitFor(() => expect(region.querySelector("pre")).toHaveTextContent("plain content here"));
  });

  it("offers no inline preview for a docx file, only the actions menu", () => {
    const docxFile = buildFile({ id: 6, rel_path: "spec.docx", format: "docx" });
    renderExplorer([docxFile]);

    expect(screen.queryByRole("button", { name: "Preview" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: `Actions for ${docxFile.rel_path}` })).toBeInTheDocument();
  });
});
