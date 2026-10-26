import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ArchiveOut } from "@/api/types";
import { SourceFiles } from "@/components/print/SourceFiles";

const { putFormMock, deleteMock, postMock } = vi.hoisted(() => ({
  putFormMock: vi.fn(),
  deleteMock: vi.fn(),
  postMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, putForm: putFormMock, delete: deleteMock, post: postMock } };
});

const base = { id: 7, model_id: 3, has_source: false, has_f3d: false } as unknown as ArchiveOut;

function wrap(a: ArchiveOut, canWrite = true) {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <SourceFiles archive={a} canWrite={canWrite} />
    </QueryClientProvider>,
  );
}

describe("SourceFiles", () => {
  beforeEach(() => {
    putFormMock.mockReset().mockResolvedValue({});
    deleteMock.mockReset().mockResolvedValue({});
    postMock.mockReset().mockResolvedValue({});
  });

  it("hides for read-only viewers with nothing attached", () => {
    wrap(base, false);
    expect(screen.queryByTestId("source-files")).toBeNull();
  });

  it("uploads a Fusion file to the f3d slot", async () => {
    wrap(base);
    const input = screen.getByTestId("source-input-f3d") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["x"], "part.f3d")] } });
    await waitFor(() => expect(putFormMock).toHaveBeenCalledWith("/archives/7/f3d", expect.any(FormData)));
  });

  it("shows a download link, linked marker, and removes a source", async () => {
    wrap({ ...base, has_source: true, source_name: "proj.3mf", source_linked: true } as ArchiveOut);
    const link = screen.getByText("proj.3mf") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/api/archives/7/source");
    expect(screen.getByText("From library model")).toBeTruthy();
    fireEvent.click(screen.getByText("Remove"));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/archives/7/source"));
  });

  it("links from the library model", async () => {
    wrap(base);
    fireEvent.click(screen.getByText("Link from library model"));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/archives/7/sources/link-model"));
  });
});
