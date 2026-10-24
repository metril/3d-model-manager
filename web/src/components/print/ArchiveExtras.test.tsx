import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { arch, renderQ } from "@/components/print/archiveTestUtils";
import { ArchiveEditor, PhotoGallery, VerdictCard } from "@/components/print/ArchiveExtras";

const m = vi.hoisted(() => ({ post: vi.fn(), patch: vi.fn(), del: vi.fn(), postForm: vi.fn() }));
vi.mock("@/api/client", async (orig) => ({
  ...(await orig<typeof import("@/api/client")>()),
  api: { post: m.post, patch: m.patch, delete: m.del, postForm: m.postForm },
}));

beforeEach(() => {
  Object.values(m).forEach((f) => f.mockReset().mockResolvedValue({}));
});

describe("VerdictCard", () => {
  it("posts a card verdict when pending", async () => {
    renderQ(<VerdictCard archive={arch({ id: 4, confirm_requested: true, user_verdict: null })} canWrite />);
    fireEvent.click(screen.getByRole("button", { name: "Bad" }));
    await waitFor(() => expect(m.post).toHaveBeenCalledWith("/archives/4/verdict", { verdict: "bad", source: "card" }));
  });

  it("shows the recorded verdict + source", () => {
    renderQ(
      <VerdictCard
        archive={arch({ confirm_requested: true, user_verdict: "good", user_verdict_source: "link" })}
        canWrite
      />,
    );
    expect(screen.getByText("Marked good")).toBeInTheDocument();
    expect(screen.getByText(/via notification link/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Good" })).toBeNull();
  });
});

describe("ArchiveEditor", () => {
  it("patches notes and tags", async () => {
    renderQ(<ArchiveEditor archive={arch({ id: 5, tags: ["a"] })} canWrite canOverride isAdmin={false} />);
    fireEvent.change(screen.getByLabelText("Notes"), { target: { value: "nice" } });
    const tag = screen.getByLabelText("Tags");
    fireEvent.change(tag, { target: { value: "b" } });
    fireEvent.keyDown(tag, { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "Save notes and tags" }));
    await waitFor(() => expect(m.patch).toHaveBeenCalledWith("/archives/5", { notes: "nice", tags: ["a", "b"] }));
  });

  it("resets a result override and hides delete for non-admins", async () => {
    renderQ(<ArchiveEditor archive={arch({ id: 5, result_override: "failed" })} canWrite canOverride isAdmin={false} />);
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    await waitFor(() => expect(m.patch).toHaveBeenCalledWith("/archives/5", { result_override: null }));
    expect(screen.queryByRole("button", { name: "Delete run" })).toBeNull();
  });

  it("admin delete goes through a confirm", async () => {
    const onDeleted = vi.fn();
    renderQ(<ArchiveEditor archive={arch({ id: 5 })} canWrite canOverride isAdmin onDeleted={onDeleted} />);
    fireEvent.click(screen.getByRole("button", { name: "Delete run" }));
    expect(m.del).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(Array.from(dialog.querySelectorAll("button")).find((b) => b.textContent === "Delete run")!);
    await waitFor(() => expect(m.del).toHaveBeenCalledWith("/archives/5"));
    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
  });
});

describe("PhotoGallery", () => {
  it("uploads the chosen file as multipart `file`", async () => {
    renderQ(<PhotoGallery archive={arch({ id: 6, photos: [] })} canWrite />);
    const file = new File(["x"], "p.png", { type: "image/png" });
    fireEvent.change(screen.getByTestId("photo-input"), { target: { files: [file] } });
    await waitFor(() => expect(m.postForm).toHaveBeenCalled());
    const [path, fd] = m.postForm.mock.calls[0] as [string, FormData];
    expect(path).toBe("/archives/6/photos");
    expect((fd.get("file") as File).name).toBe("p.png");
  });

  it("rejects wrong types without calling the api", () => {
    renderQ(<PhotoGallery archive={arch({ id: 6, photos: [] })} canWrite />);
    fireEvent.change(screen.getByTestId("photo-input"), {
      target: { files: [new File(["x"], "p.gif", { type: "image/gif" })] },
    });
    expect(m.postForm).not.toHaveBeenCalled();
  });

  it("lists photos and deletes after confirm", async () => {
    const photos = [{ filename: "a.jpg", kind: "photo" as const, uploaded_by: 1, at: "2026-10-20T00:00:00Z" }];
    renderQ(<PhotoGallery archive={arch({ id: 6, photos })} canWrite />);
    expect(screen.getAllByRole("img")[0]).toHaveAttribute("src", "/api/archives/6/photos/a.jpg");
    fireEvent.click(screen.getByRole("button", { name: "Delete photo" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(Array.from(dialog.querySelectorAll("button")).find((b) => b.textContent === "Delete photo")!);
    await waitFor(() => expect(m.del).toHaveBeenCalledWith("/archives/6/photos/a.jpg"));
  });
});
