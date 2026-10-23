import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { JobUpdatedEvent, UploadResult } from "@/api/types";
import { UploadDropzone, type UploadTarget } from "@/components/upload/UploadDropzone";

// `vi.mock` factories are hoisted above the module's own top-level bindings
// (same pattern as UploadPage.test.tsx), so the fakes have to be created
// through `vi.hoisted`.
const { uploadFileMock, eventsListener } = vi.hoisted(() => ({
  uploadFileMock: vi.fn(),
  // Holds the callback this component's `useEvents().subscribe(...)`
  // registers, so the SSE-race test can fire a `job.updated` event directly
  // instead of the no-op stub silently discarding it.
  eventsListener: { current: null as ((event: JobUpdatedEvent) => void) | null },
}));

vi.mock("@/api/upload", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/upload")>();
  return { ...actual, uploadFile: uploadFileMock };
});

vi.mock("@/hooks/eventsContext", () => ({
  useEvents: () => ({
    subscribe: (listener: (event: JobUpdatedEvent) => void) => {
      eventsListener.current = listener;
      return () => {
        eventsListener.current = null;
      };
    },
  }),
}));

const TARGET: UploadTarget = { modelId: 7, revisionId: 70 };

function fakeUploadResult(jobId: string): UploadResult {
  return { file_id: 1, blob_hash: "abc123", size: 5, job_id: jobId };
}

function addFileToQueue(container: HTMLElement, name: string) {
  const input = container.querySelector('input[type="file"]');
  if (!(input instanceof HTMLInputElement)) throw new Error("file input not found");
  const file = new File([name], name);
  fireEvent.change(input, { target: { files: [file] } });
}

describe("UploadDropzone", () => {
  beforeEach(() => {
    uploadFileMock.mockReset();
    eventsListener.current = null;
  });

  it("queues a selected file as pending, then uploads it to the resolved target on click", async () => {
    uploadFileMock.mockResolvedValueOnce(fakeUploadResult("job-1"));
    const resolveTarget = vi.fn().mockResolvedValue(TARGET);

    const { container } = render(<UploadDropzone resolveTarget={resolveTarget} />);
    addFileToQueue(container, "a.stl");
    expect(screen.getByText("Pending")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Upload/ }));

    await waitFor(() => expect(uploadFileMock).toHaveBeenCalledTimes(1));
    expect(uploadFileMock.mock.calls[0][0]).toMatchObject({ modelId: 7, revisionId: 70, relPath: "a.stl" });

    // Lands on "Processing" (not "Stored") because no terminal `job.updated`
    // SSE event has fired -- that transition is covered separately below.
    await waitFor(() => expect(screen.getByText("Processing")).toBeInTheDocument());
  });

  it("does not call uploadFile, or otherwise touch the queue, when resolveTarget resolves to null", async () => {
    const resolveTarget = vi.fn().mockResolvedValue(null);

    const { container } = render(<UploadDropzone resolveTarget={resolveTarget} />);
    addFileToQueue(container, "a.stl");
    fireEvent.click(screen.getByRole("button", { name: /^Upload/ }));

    await waitFor(() => expect(resolveTarget).toHaveBeenCalledTimes(1));
    expect(uploadFileMock).not.toHaveBeenCalled();
    expect(screen.getByText("Pending")).toBeInTheDocument();
  });

  it("calls onUploadComplete once per batch, bracketed by onUploadingChange(true) then (false)", async () => {
    uploadFileMock.mockResolvedValueOnce(fakeUploadResult("job-1"));
    const resolveTarget = vi.fn().mockResolvedValue(TARGET);
    const onUploadComplete = vi.fn();
    const onUploadingChange = vi.fn();

    const { container } = render(
      <UploadDropzone
        resolveTarget={resolveTarget}
        onUploadComplete={onUploadComplete}
        onUploadingChange={onUploadingChange}
      />,
    );
    addFileToQueue(container, "a.stl");
    fireEvent.click(screen.getByRole("button", { name: /^Upload/ }));

    await waitFor(() => expect(onUploadComplete).toHaveBeenCalledTimes(1));
    expect(onUploadingChange.mock.calls).toEqual([[true], [false]]);
  });

  it("keeps the Upload button disabled while the external `disabled` prop is set, even with pending files", () => {
    const { container } = render(<UploadDropzone resolveTarget={vi.fn()} disabled />);
    addFileToQueue(container, "a.stl");

    expect(screen.getByRole("button", { name: /^Upload/ })).toBeDisabled();
  });

  it("still lands on 'stored' when the terminal SSE event races ahead of the upload's PUT response", async () => {
    let resolveUpload: (result: UploadResult) => void = () => {};
    uploadFileMock.mockReturnValueOnce(
      new Promise<UploadResult>((resolve) => {
        resolveUpload = resolve;
      }),
    );
    const resolveTarget = vi.fn().mockResolvedValue(TARGET);

    const { container } = render(<UploadDropzone resolveTarget={resolveTarget} />);
    addFileToQueue(container, "a.stl");
    fireEvent.click(screen.getByRole("button", { name: /^Upload/ }));

    await waitFor(() => expect(uploadFileMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(eventsListener.current).not.toBeNull());

    act(() => {
      eventsListener.current?.({
        type: "job.updated",
        job_id: "job-1",
        job_type: "convert_to_glb",
        state: "done",
        subject_type: "file",
        subject_id: 1,
      });
    });

    resolveUpload(fakeUploadResult("job-1"));

    await waitFor(() => expect(screen.getByText("Stored")).toBeInTheDocument());
  });

  it("shows the duplicate card on a 409 and retries with allow_duplicate via 'Upload anyway'", async () => {
    const { DuplicateUploadError } = await import("@/api/upload");
    uploadFileMock.mockRejectedValueOnce(
      new DuplicateUploadError({
        detail: "duplicate",
        existing: { slug: "dragon", name: "Dragon", url: "/models/dragon" },
        suggested_name: "a (2)",
      }),
    );
    uploadFileMock.mockResolvedValueOnce(fakeUploadResult("job-2"));
    const resolveTarget = vi.fn().mockResolvedValue(TARGET);

    const { container } = render(<UploadDropzone resolveTarget={resolveTarget} />);
    addFileToQueue(container, "a.stl");
    fireEvent.click(screen.getByRole("button", { name: /^Upload/ }));

    expect(await screen.findByText("Dragon")).toBeInTheDocument();
    expect(uploadFileMock).toHaveBeenCalledTimes(1);
    expect(uploadFileMock.mock.calls[0][0]).not.toHaveProperty("allowDuplicate", true);

    fireEvent.click(screen.getByRole("button", { name: "Upload anyway" }));

    await waitFor(() => expect(uploadFileMock).toHaveBeenCalledTimes(2));
    expect(uploadFileMock.mock.calls[1][0]).toMatchObject({ allowDuplicate: true });
    expect(await screen.findByText("Processing")).toBeInTheDocument();
  });
});
