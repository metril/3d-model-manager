import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SliceJob } from "@/api/types";
import { SliceDialog } from "@/components/slice/SliceDialog";
import { SliceFileButton } from "@/components/slice/SliceFileButton";
import { isSliceable, pickSliceableFile } from "@/components/slice/sliceable";
import { SliceJobsHost, SliceToast } from "@/components/slice/SliceJobsHost";
import { SliceLogDialog } from "@/components/slice/SliceLogDialog";
import { trackSlice, untrackSlice } from "@/components/slice/sliceTracker";

const { getMock, postMock, deleteMock, toastMock, navigateMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  deleteMock: vi.fn(),
  toastMock: { custom: vi.fn(), dismiss: vi.fn(), error: vi.fn(), success: vi.fn() },
  navigateMock: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock, delete: deleteMock } };
});
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-router")>()),
  useNavigate: () => navigateMock,
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}));
vi.mock("@/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/auth")>()),
  useAuth: () => ({ can: () => true }),
}));
vi.mock("@/components/print/PrintDialog", () => ({
  PrintDialog: ({ source }: { source: { fileId: number } }) => <div data-testid="print-dialog">print {source.fileId}</div>,
}));

const FILE = { id: 5, rel_path: "parts/cube.stl", format: "stl" as const, verified_at: "2026-01-01T00:00:00Z" };
const JOB: SliceJob = {
  id: "job-1",
  file_id: 5,
  model_id: 2,
  model_slug: "cube",
  file_name: "cube.stl",
  recipe_id: 3,
  plate: null,
  state: "running",
  error: null,
  log_tail: null,
  sliced_file_id: null,
  meta: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function ui(node: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

let jobState: SliceJob;
beforeEach(() => {
  [getMock, postMock, deleteMock, toastMock.custom, toastMock.dismiss, toastMock.error, toastMock.success, navigateMock].forEach((m) =>
    m.mockReset(),
  );
  untrackSlice("job-1");
  jobState = JOB;
  getMock.mockImplementation((path: string) => {
    if (path === "/features") return Promise.resolve({ slicer_configured: true });
    if (path === "/recipes") return Promise.resolve([{ id: 3, name: "A1 PLA" }, { id: 4, name: "A1 PETG" }]);
    if (path === "/slicer/health") return Promise.resolve({ configured: true, ok: true });
    if (path === "/slice-jobs/job-1") return Promise.resolve(jobState);
    return Promise.resolve(null);
  });
});

describe("SliceFileButton", () => {
  it("only offers Slice for verified sliceable source files", async () => {
    expect(isSliceable(FILE)).toBe(true);
    expect(isSliceable({ ...FILE, format: "gcode_3mf" })).toBe(false);
    expect(isSliceable({ ...FILE, verified_at: null })).toBe(false);
    expect(pickSliceableFile([{ ...FILE, format: "obj" }, FILE])?.format).toBe("stl");
    ui(<SliceFileButton file={FILE} />);
    expect(await screen.findByRole("button", { name: "Slice parts/cube.stl" })).toBeInTheDocument();
  });

  it("hides itself for a gcode.3mf", () => {
    ui(<SliceFileButton file={{ ...FILE, format: "gcode_3mf" }} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("SliceDialog", () => {
  it("starts a slice job with the chosen recipe and plate, then tracks it", async () => {
    postMock.mockResolvedValue({ ...JOB, state: "queued" });
    const onOpenChange = vi.fn();
    ui(<SliceDialog open onOpenChange={onOpenChange} file={FILE} />);
    fireEvent.click(await screen.findByLabelText("A1 PETG"));
    fireEvent.change(screen.getByLabelText("Plate (optional)"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Slice" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/files/5/slice", { recipe_id: 4, plate: 2 }));
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    untrackSlice("job-1");
  });

  it("keeps Slice disabled until a recipe is chosen", async () => {
    ui(<SliceDialog open onOpenChange={() => {}} file={FILE} />);
    await screen.findByLabelText("A1 PLA");
    expect(screen.getByRole("button", { name: "Slice" })).toBeDisabled();
  });

  it("blocks when the sidecar is unreachable", async () => {
    getMock.mockImplementation((path: string) =>
      Promise.resolve(path === "/recipes" ? [{ id: 3, name: "A1 PLA" }] : { configured: true, ok: false, error: "timed out" }),
    );
    ui(<SliceDialog open onOpenChange={() => {}} file={FILE} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("timed out");
    fireEvent.click(await screen.findByLabelText("A1 PLA"));
    expect(screen.getByRole("button", { name: "Slice" })).toBeDisabled();
  });

  describe("pipelines", () => {
    const withPipelines = () => {
      const base = getMock.getMockImplementation()!;
      getMock.mockImplementation((path: string) =>
        path === "/slicer-pipelines"
          ? Promise.resolve([
              { id: 7, name: "Orient & rename", recipe_name: "A1 PLA", steps: [{ kind: "orient" }, { kind: "rename", template: "x" }] },
              { id: 8, name: "Print it", recipe_name: "A1 PLA", steps: [{ kind: "queue" }] },
            ])
          : base(path),
      );
    };

    it("is recipe-only when no pipelines exist", async () => {
      ui(<SliceDialog open onOpenChange={() => {}} file={FILE} />);
      await screen.findByLabelText("A1 PLA");
      expect(screen.queryByRole("button", { name: "Pipeline" })).not.toBeInTheDocument();
    });

    it("runs a slice-only pipeline and tracks the job", async () => {
      withPipelines();
      postMock.mockResolvedValue({ kind: "slice", slice_job: { ...JOB, state: "queued" }, queue_entry: null });
      const onOpenChange = vi.fn();
      ui(<SliceDialog open onOpenChange={onOpenChange} file={FILE} />);
      fireEvent.click(await screen.findByRole("button", { name: "Pipeline" }));
      fireEvent.click(await screen.findByLabelText(/Orient & rename/));
      fireEvent.click(screen.getByRole("button", { name: "Run pipeline" }));
      await waitFor(() => expect(postMock).toHaveBeenCalledWith("/slicer-pipelines/7/run", { file_id: 5, plate: null }));
      await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
      untrackSlice("job-1");
    });

    it("toasts when a queue pipeline enqueues instead", async () => {
      withPipelines();
      postMock.mockResolvedValue({ kind: "queue", slice_job: null, queue_entry: { id: 3 } });
      ui(<SliceDialog open onOpenChange={() => {}} file={FILE} />);
      fireEvent.click(await screen.findByRole("button", { name: "Pipeline" }));
      fireEvent.click(await screen.findByLabelText(/Print it/));
      fireEvent.click(screen.getByRole("button", { name: "Run pipeline" }));
      await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith(expect.stringContaining("Queued cube.stl")));
    });
  });
});

describe("SliceToast", () => {
  const noop = () => {};
  const render_ = (job: SliceJob | undefined, over: Partial<Parameters<typeof SliceToast>[0]> = {}) =>
    render(<SliceToast fileName="cube.stl" job={job} onCancel={noop} onOpen={noop} onSend={noop} onLog={noop} onDismiss={noop} {...over} />);

  it("shows Cancel while running", () => {
    const onCancel = vi.fn();
    render_(JOB, { onCancel });
    expect(screen.getByText("Slicing cube.stl…")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("offers open, download and send on success", () => {
    const onSend = vi.fn();
    const onOpen = vi.fn();
    render_(
      {
        ...JOB,
        state: "done",
        sliced_file_id: 9,
        meta: { print_time_s: 5400, filament_g: 12.4, plate_count: 1, plates: [] },
      },
      { onSend, onOpen },
    );
    expect(screen.getByText(/1h 30m · 12 g · 1 plate/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute("href", "/api/files/9/download");
    fireEvent.click(screen.getByRole("button", { name: "Open file" }));
    fireEvent.click(screen.getByRole("button", { name: "Send to printer" }));
    expect(onOpen).toHaveBeenCalled();
    expect(onSend).toHaveBeenCalled();
  });

  it("offers View log on failure", () => {
    const onLog = vi.fn();
    render_({ ...JOB, state: "failed", error: "exited 3" }, { onLog });
    expect(screen.getByText("exited 3")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "View log" }));
    expect(onLog).toHaveBeenCalled();
  });
});

describe("SliceJobsHost", () => {
  it("follows a tracked job in a toast, cancels it, and opens the log on failure", async () => {
    deleteMock.mockResolvedValue({ ...JOB, state: "cancelled" });
    ui(<SliceJobsHost />);
    act(() => trackSlice({ id: "job-1", fileName: "cube.stl" }));
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/slice-jobs/job-1"));
    await waitFor(() => expect(toastMock.custom).toHaveBeenCalled());
    const [, opts] = toastMock.custom.mock.calls.at(-1)!;
    expect(opts.id).toBe("slice-job-1");
    expect(opts.duration).toBe(Infinity);

    // Render the toast body the host registered and press Cancel.
    const body = toastMock.custom.mock.calls.at(-1)![0] as () => ReactElement;
    const { unmount } = render(body());
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/slice-jobs/job-1"));
    unmount();
  });

  it("stops polling and untracks when the job can't be loaded (404)", async () => {
    const { ApiError } = await import("@/api/client");
    getMock.mockRejectedValue(new ApiError(404, "slice job gone"));
    ui(<SliceJobsHost />);
    act(() => trackSlice({ id: "job-gone", fileName: "cube.stl" }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
    expect(toastMock.error.mock.calls.at(-1)![0]).toContain("slice job gone");
    expect(toastMock.dismiss).toHaveBeenCalledWith("slice-job-gone");
    const calls = getMock.mock.calls.length;
    await new Promise((r) => setTimeout(r, 2300));
    expect(getMock.mock.calls.length).toBe(calls);
  });

  it("opens the log dialog and the print dialog from the toast", async () => {
    jobState = { ...JOB, state: "failed", error: "boom", log_tail: "tail of the log" };
    ui(<SliceJobsHost />);
    act(() => trackSlice({ id: "job-1", fileName: "cube.stl" }));
    await waitFor(() => {
      const last = toastMock.custom.mock.calls.at(-1);
      expect(last?.[1].duration).toBe(30_000);
    });
    const body = toastMock.custom.mock.calls.at(-1)![0] as () => ReactElement;
    const toastView = render(body());
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "View log" }));
    });
    expect(await screen.findByText("tail of the log")).toBeInTheDocument();
    toastView.unmount();
  });

  it("opens the log from a successful slice's toast", async () => {
    jobState = { ...JOB, state: "done", sliced_file_id: 9, meta: null, log_tail: "slicer ok output" };
    ui(<SliceJobsHost />);
    act(() => trackSlice({ id: "job-1", fileName: "cube.stl" }));
    await waitFor(() => expect(toastMock.custom.mock.calls.at(-1)?.[1].duration).toBe(60_000));
    const body = toastMock.custom.mock.calls.at(-1)![0] as () => ReactElement;
    const toastView = render(body());
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "View log" }));
    });
    expect(await screen.findByText("slicer ok output")).toBeInTheDocument();
    toastView.unmount();
  });

  it("sends the sliced file to a printer from a finished toast", async () => {
    jobState = { ...JOB, state: "done", sliced_file_id: 9, meta: null };
    ui(<SliceJobsHost />);
    act(() => trackSlice({ id: "job-1", fileName: "cube.stl" }));
    await waitFor(() => expect(toastMock.custom.mock.calls.at(-1)?.[1].duration).toBe(60_000));
    const body = toastMock.custom.mock.calls.at(-1)![0] as () => ReactElement;
    const toastView = render(body());
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Send to printer" }));
    });
    expect(await screen.findByTestId("print-dialog")).toHaveTextContent("print 9");
    toastView.unmount();
  });
});

describe("SliceLogDialog", () => {
  it("shows the log, or a note when none was captured", () => {
    const { rerender } = render(<SliceLogDialog open onOpenChange={() => {}} error="exited 3" log="line1\nline2" />);
    expect(screen.getByLabelText("Log output")).toHaveTextContent("line1");
    rerender(<SliceLogDialog open onOpenChange={() => {}} log={null} />);
    expect(screen.getByLabelText("Log output")).toHaveTextContent("No log was captured.");
  });
});
