import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { arch, renderQ } from "@/components/print/archiveTestUtils";
import { TimelapseEditorDialog } from "@/components/print/TimelapseEditorDialog";

const m = vi.hoisted(() => ({ get: vi.fn(), postForm: vi.fn(), toastOk: vi.fn(), toastErr: vi.fn() }));
vi.mock("@/api/client", async (orig) => ({
  ...(await orig<typeof import("@/api/client")>()),
  api: { get: m.get, postForm: m.postForm },
}));
vi.mock("sonner", () => ({ toast: { success: m.toastOk, error: m.toastErr } }));

beforeEach(() => {
  m.get.mockReset();
  m.postForm.mockReset();
  m.toastOk.mockReset();
  m.toastErr.mockReset();
  m.get.mockImplementation(async (path: string) => {
    if (path.endsWith("/info")) return { duration_s: 20, fps: 30, width: 1, height: 1, bytes: 1 };
    if (path.includes("/thumbnails")) return { frames: ["data:image/jpeg;base64,AA=="] };
    if (path.includes("/jobs/")) return { state: "done", error: null, filename: null };
    return {};
  });
  m.postForm.mockResolvedValue({ job_id: "j1" });
});

describe("TimelapseEditorDialog", () => {
  it("submits trim/speed/mode as a form, polls the job, then toasts", async () => {
    const onOpenChange = vi.fn();
    renderQ(<TimelapseEditorDialog archive={arch({ id: 8, has_timelapse: true })} open onOpenChange={onOpenChange} />);
    await screen.findByTestId("thumb-strip");
    await waitFor(() => expect(screen.getByRole("button", { name: "Process" })).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Trim start: 0.0 s"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Speed"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Save as"), { target: { value: "new" } });
    fireEvent.click(screen.getByRole("button", { name: "Process" }));
    await waitFor(() => expect(m.postForm).toHaveBeenCalled());
    const [path, fd] = m.postForm.mock.calls[0] as [string, FormData];
    expect(path).toBe("/archives/8/timelapse/process");
    expect(Object.fromEntries(fd.entries())).toEqual({ trim_start: "2", speed: "2", save_mode: "new" });
    await waitFor(() => expect(m.get).toHaveBeenCalledWith("/archives/8/timelapse/jobs/j1"));
    await waitFor(() => expect(m.toastOk).toHaveBeenCalledWith("Timelapse updated"));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("stops polling and toasts when the job status request fails", async () => {
    const jobCalls = () => m.get.mock.calls.filter((c) => String(c[0]).includes("/jobs/")).length;
    m.get.mockImplementation(async (path: string) => {
      if (path.endsWith("/info")) return { duration_s: 20, fps: 30, width: 1, height: 1, bytes: 1 };
      if (path.includes("/thumbnails")) return { frames: [] };
      throw new Error("boom");
    });
    renderQ(<TimelapseEditorDialog archive={arch({ id: 8, has_timelapse: true })} open onOpenChange={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Process" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Process" }));
    await waitFor(() => expect(m.toastErr).toHaveBeenCalled());
    const n = jobCalls();
    await new Promise((r) => setTimeout(r, 1800));
    expect(jobCalls()).toBe(n);
    expect(screen.getByRole("button", { name: "Process" })).toBeEnabled();
  });
});
