import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import { modelQueryOptions } from "@/api/library";
import { FileExplorer } from "@/components/model-detail/explorer/FileExplorer";
import type { FileOut, ModelDetail } from "@/api/types";

const authState = vi.hoisted(() => ({ admin: true }));
vi.mock("@/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/auth")>()),
  useAuth: () => ({
    data: { id: 1, username: "tester" },
    can: (p?: string) => (p === "admin" ? authState.admin : true),
    canPrinter: () => true,
  }),
}));

const { getMock, patchMock, postMock, deleteMock, uploadFileMock, toast } = vi.hoisted(() => ({
  getMock: vi.fn(),
  patchMock: vi.fn(),
  postMock: vi.fn(),
  deleteMock: vi.fn(),
  uploadFileMock: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, patch: patchMock, post: postMock, delete: deleteMock } };
});
vi.mock("@/api/upload", () => ({ uploadFile: uploadFileMock }));
vi.mock("@/hooks/eventsContext", () => ({ useEvents: () => ({ subscribe: () => () => {} }) }));
vi.mock("sonner", () => ({ toast }));

const DONE_JOB = { id: "j1", state: "done", error: null };

function file(id: number, rel_path: string): FileOut {
  return {
    id,
    revision_id: 1,
    rel_path,
    storage_path: `/data/${rel_path}`,
    blob_hash: "abc123def456",
    size: 100,
    format: "stl",
    kind: "mesh",
    mtime: "2026-06-01T12:00:00Z",
    verified_at: "2026-06-01T12:00:05Z",
    meta: null,
    thumb_ready: false,
    glb_status: null,
    glb_preview_ready: false,
  };
}

function model(files: FileOut[]): ModelDetail {
  return {
    id: 7,
    slug: "m",
    name: "M",
    tags: [],
    notes: [],
    backends: [],
    current_revision: { id: 1, model_id: 7, number: 1, name: null, note: null, dir_name: "r1", created_at: "", files, notes: [] },
  } as unknown as ModelDetail;
}

let queryClient: QueryClient;
function renderExplorer(files: FileOut[]) {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <FileExplorer model={model(files)} />
    </QueryClientProvider>,
  );
}

const FILES = [file(1, "a.stl"), file(2, "sub/b.stl"), file(3, "sub/c.stl")];

function openManage(name: string, index = 0) {
  fireEvent.pointerDown(screen.getAllByRole("button", { name: `Manage ${name}` })[index], { button: 0 });
}

beforeEach(() => {
  for (const m of [getMock, patchMock, postMock, deleteMock, uploadFileMock, toast.success, toast.error]) m.mockReset();
  getMock.mockResolvedValue(undefined);
  authState.admin = true;
});

describe("rename", () => {
  it("renames a file with PATCH and invalidates the model and storage queries", async () => {
    patchMock.mockResolvedValue(DONE_JOB);
    renderExplorer(FILES);
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    openManage("a.stl");
    fireEvent.click(await screen.findByRole("menuitem", { name: "Rename" }));
    const input = screen.getByLabelText("New name");
    fireEvent.change(input, { target: { value: "z.stl" } });
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(patchMock).toHaveBeenCalledWith("/files/1", { rel_path: "z.stl" }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Moved 1 file"));
    expect(spy).toHaveBeenCalledWith({ queryKey: modelQueryOptions("m").queryKey });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["storage"] });
  });

  it("rewrites every descendant's prefix when renaming a folder (batch move)", async () => {
    postMock.mockResolvedValue(DONE_JOB);
    renderExplorer(FILES);
    openManage("sub");
    fireEvent.click(await screen.findByRole("menuitem", { name: "Rename" }));
    fireEvent.change(screen.getByLabelText("New name"), { target: { value: "parts" } });
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/models/7/files/move", {
        moves: [
          { file_id: 2, rel_path: "parts/b.stl" },
          { file_id: 3, rel_path: "parts/c.stl" },
        ],
      }),
    );
  });

  it("validates the name client-side, including case-insensitive sibling clashes", async () => {
    renderExplorer(FILES);
    openManage("a.stl");
    fireEvent.click(await screen.findByRole("menuitem", { name: "Rename" }));
    const input = screen.getByLabelText("New name");
    const submit = screen.getByRole("button", { name: "Rename" });
    fireEvent.change(input, { target: { value: "x/y" } });
    expect(screen.getByRole("alert")).toHaveTextContent("can't contain");
    expect(submit).toBeDisabled();
    fireEvent.change(input, { target: { value: "SUB" } });
    expect(screen.getByRole("alert")).toHaveTextContent("already exists");
    fireEvent.change(input, { target: { value: "_snapshots" } });
    expect(screen.getByRole("alert")).toHaveTextContent("reserved");
    fireEvent.change(input, { target: { value: ".." } });
    expect(submit).toBeDisabled();
    expect(patchMock).not.toHaveBeenCalled();
  });

  it("shows the backend detail in a toast when the server refuses", async () => {
    patchMock.mockRejectedValue(new ApiError(409, "a file already exists at that path"));
    renderExplorer(FILES);
    openManage("a.stl");
    fireEvent.click(await screen.findByRole("menuitem", { name: "Rename" }));
    fireEvent.change(screen.getByLabelText("New name"), { target: { value: "q.stl" } });
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("a file already exists at that path"));
  });

  it("surfaces a failed job's error", async () => {
    patchMock.mockResolvedValue({ id: "j2", state: "failed", error: "hash mismatch" });
    renderExplorer(FILES);
    openManage("a.stl");
    fireEvent.click(await screen.findByRole("menuitem", { name: "Rename" }));
    fireEvent.change(screen.getByLabelText("New name"), { target: { value: "q.stl" } });
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("hash mismatch"));
  });
});

describe("move dialog", () => {
  it("moves the selection into a picked folder", async () => {
    postMock.mockResolvedValue(DONE_JOB);
    renderExplorer([file(1, "a.stl"), file(4, "d.stl"), file(2, "sub/b.stl")]);
    fireEvent.click(screen.getByRole("checkbox", { name: "Select a.stl" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select d.stl" }));
    fireEvent.click(screen.getByRole("button", { name: "Move to…" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Move here" })).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("button", { name: /sub/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Move here" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/models/7/files/move", {
        moves: [
          { file_id: 1, rel_path: "sub/a.stl" },
          { file_id: 4, rel_path: "sub/d.stl" },
        ],
      }),
    );
  });

  it("does not offer a folder as its own destination", async () => {
    renderExplorer(FILES);
    fireEvent.click(screen.getByRole("checkbox", { name: "Select folder sub" }));
    fireEvent.click(screen.getByRole("button", { name: "Move to…" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("button", { name: /sub/ })).not.toBeInTheDocument();
    expect(within(dialog).getByText("No subfolders here.")).toBeInTheDocument();
  });
});

describe("drag and drop", () => {
  function fakeDataTransfer() {
    const store = new Map<string, string>();
    return {
      store,
      setData: (k: string, v: string) => store.set(k, v),
      getData: (k: string) => store.get(k) ?? "",
      get types() {
        return [...store.keys()];
      },
      effectAllowed: "",
      dropEffect: "",
    };
  }

  it("moves a dragged file onto a folder entry", async () => {
    patchMock.mockResolvedValue(DONE_JOB);
    renderExplorer(FILES);
    const dt = fakeDataTransfer();
    const row = screen.getByText("a.stl").closest("li")!;
    fireEvent.dragStart(row, { dataTransfer: dt });
    const target = screen.getByText("sub/").closest("li")!;
    fireEvent.dragOver(target, { dataTransfer: dt });
    expect(target).toHaveAttribute("data-drop-over", "true");
    fireEvent.drop(target, { dataTransfer: dt });
    await waitFor(() => expect(patchMock).toHaveBeenCalledWith("/files/1", { rel_path: "sub/a.stl" }));
  });

  it("moves a dragged folder onto a tree node and ignores OS file drops", async () => {
    postMock.mockResolvedValue(DONE_JOB);
    renderExplorer([file(1, "a/x.stl"), file(2, "a/y.stl"), file(3, "b/z.stl")]);
    const dt = fakeDataTransfer();
    fireEvent.dragStart(screen.getByText("a/").closest("li")!, { dataTransfer: dt });
    const tree = screen.getByRole("tree");
    const node = within(tree).getByText("b").closest("div")!;
    fireEvent.drop(node, { dataTransfer: { ...fakeDataTransfer(), types: ["Files"] } });
    expect(postMock).not.toHaveBeenCalled();
    fireEvent.drop(node, { dataTransfer: dt });
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/models/7/files/move", {
        moves: [
          { file_id: 1, rel_path: "b/a/x.stl" },
          { file_id: 2, rel_path: "b/a/y.stl" },
        ],
      }),
    );
  });

  it("refuses to drop a folder into itself with a toast", () => {
    renderExplorer([file(1, "a/x.stl"), file(2, "a/in/y.stl")]);
    const dt = fakeDataTransfer();
    fireEvent.click(screen.getByRole("button", { name: "Expand a" }));
    fireEvent.dragStart(screen.getByText("a/").closest("li")!, { dataTransfer: dt });
    fireEvent.drop(within(screen.getByRole("tree")).getByText("in").closest("div")!, { dataTransfer: dt });
    expect(toast.error).toHaveBeenCalledWith("A folder can't be moved into itself.");
    expect(postMock).not.toHaveBeenCalled();
  });
});

describe("new folder", () => {
  it("is client-only until a file is uploaded into it, and uploads are prefixed with it", async () => {
    uploadFileMock.mockResolvedValue({ file_id: 9, blob_hash: "h", size: 5, job_id: "job-1" });
    renderExplorer(FILES);
    fireEvent.click(screen.getByRole("button", { name: "New folder" }));
    expect(screen.getByText(/only created on the server once a file is uploaded/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Folder name"), { target: { value: "fresh" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(screen.getByText(/only exists in your browser/)).toBeInTheDocument();
    expect(patchMock).not.toHaveBeenCalled();
    expect(postMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Add files" }));
    expect(screen.getByText("Uploading into fresh")).toBeInTheDocument();
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["b"], "part.stl")] } });
    fireEvent.click(screen.getByRole("button", { name: /^Upload/ }));
    await waitFor(() => expect(uploadFileMock).toHaveBeenCalledTimes(1));
    expect(uploadFileMock.mock.calls[0][0].relPath).toBe("fresh/part.stl");
  });

  it("shows the folder in its parent and rejects clashing names", () => {
    renderExplorer(FILES);
    fireEvent.click(screen.getByRole("button", { name: "New folder" }));
    fireEvent.change(screen.getByLabelText("Folder name"), { target: { value: "SUB" } });
    expect(screen.getByRole("alert")).toHaveTextContent("already exists");
    expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Folder name"), { target: { value: "fresh" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    fireEvent.click(screen.getByRole("button", { name: "Files" }));
    expect(screen.getByText("fresh/")).toBeInTheDocument();
  });
});

describe("bulk delete", () => {
  it("confirms with a count, then deletes every file (folders expand to their files)", async () => {
    deleteMock.mockResolvedValue(undefined);
    renderExplorer(FILES);
    fireEvent.click(screen.getByRole("checkbox", { name: "Select a.stl" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select folder sub" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("Delete 3 files?")).toBeInTheDocument();
    expect(deleteMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Delete 3 files" }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledTimes(3));
    expect(deleteMock.mock.calls.map((c) => c[0]).sort()).toEqual(["/files/1", "/files/2", "/files/3"]);
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Deleted 3 files"));
  });

  it("reports partial failures with the backend detail", async () => {
    deleteMock.mockImplementation((path: string) =>
      path === "/files/2" ? Promise.reject(new ApiError(403, "not allowed")) : Promise.resolve(undefined),
    );
    renderExplorer(FILES);
    fireEvent.click(screen.getByRole("checkbox", { name: "Select folder sub" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete 2 files" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("1 of 2 failed: not allowed")));
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("is hidden without upload permission", () => {
    authState.admin = false;
    const base = model(FILES);
    queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <FileExplorer model={{ ...base, created_by_user_id: null }} />
      </QueryClientProvider>,
    );
    expect(screen.queryByRole("checkbox", { name: "Select a.stl" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New folder" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Manage a.stl" })).not.toBeInTheDocument();
  });
});
