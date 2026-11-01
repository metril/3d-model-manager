import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import { serverSettingsQueryOptions } from "@/api/serverSettings";
import type { OrcaSlicerSession } from "@/api/orcaslicer";
import type { FileOut, ModelDetail } from "@/api/types";
import { FileActionsMenu } from "@/components/model-detail/FileActionsMenu";
import { fakeServer } from "@/components/settings/server/fixtures";
import { OrcaSlicerServerCard } from "@/components/settings/server/OrcaSlicerServerCard";
import { OrcaSlicerFrame } from "@/components/orcaslicer/OrcaSlicerFrame";
import { TooltipProvider } from "@/components/ui/tooltip";

const { getMock, postMock, putMock, deleteMock, toastMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  putMock: vi.fn(),
  deleteMock: vi.fn(),
  toastMock: { success: vi.fn(), error: vi.fn() },
}));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock, put: putMock, delete: deleteMock } };
});
vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), toastMock) }));
const openMock = vi.fn();
const closeMock = vi.fn();

const session: OrcaSlicerSession = {
  token: "tok1",
  model_slug: "m",
  model_id: 1,
  revision_id: 1,
  file: { id: 1, name: "a.3mf" },
  created_at: "2026-10-30T00:00:00Z",
  expires_at: "2026-10-30T08:00:00Z",
  orcaslicer_running: true,
};

function mockGet({ enabled = true, sess = null as OrcaSlicerSession | null, admin = true } = {}) {
  getMock.mockImplementation((path: string) => {
    if (path === "/auth/me") return Promise.resolve({ id: 1, role: admin ? "admin" : "member", permissions: [] });
    if (path === "/features") return Promise.resolve({ printer_enabled: false, watch_dir: null, watch_enabled: false, orcaslicer_configured: enabled });
    if (path === "/orcaslicer/session") return Promise.resolve(sess ?? undefined);
    return Promise.resolve(undefined);
  });
}

function wrap(ui: React.ReactNode, qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

function file(overrides: Partial<FileOut> = {}): FileOut {
  return {
    id: 2,
    revision_id: 1,
    rel_path: "b.stl",
    storage_path: "/b.stl",
    blob_hash: "h",
    size: 1,
    format: "stl",
    kind: "mesh",
    mtime: null,
    verified_at: "2026-10-30T00:00:00Z",
    meta: null,
    thumb_ready: false,
    glb_status: null,
    glb_preview_ready: false,
    ...overrides,
  };
}
const model = { id: 1, slug: "m", name: "M" } as ModelDetail;

beforeEach(() => {
  for (const m of [getMock, postMock, putMock, deleteMock, openMock, closeMock, toastMock.success, toastMock.error]) m.mockReset();
  openMock.mockReturnValue({ close: closeMock });
  vi.stubGlobal("open", openMock);
  localStorage.clear();
});

describe("OrcaSlicerServerCard", () => {
  const data = fakeServer({ orcaslicer_enabled: true, orcaslicer_launcher_token: "***" });
  function renderCard() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(serverSettingsQueryOptions.queryKey, data);
    wrap(<OrcaSlicerServerCard data={data} />, qc);
  }

  it("keeps the masked token when untouched", async () => {
    putMock.mockImplementation((_p: string, body: unknown) => Promise.resolve({ ...data, config: body }));
    renderCard();
    fireEvent.change(screen.getByLabelText("OrcaSlicer URL"), { target: { value: "http://orcaslicer2:3000" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(putMock).toHaveBeenCalled());
    expect(putMock.mock.calls[0][1]).toMatchObject({
      orcaslicer_enabled: true,
      orcaslicer_url: "http://orcaslicer2:3000",
      orcaslicer_launcher_token: "***",
    });
  });

  it("sends a new token and null when cleared", async () => {
    putMock.mockImplementation((_p: string, body: unknown) => Promise.resolve({ ...data, config: body }));
    renderCard();
    const tok = screen.getByLabelText("Launcher token");
    fireEvent.focus(tok);
    fireEvent.change(tok, { target: { value: "s3" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(putMock).toHaveBeenCalledTimes(1));
    expect(putMock.mock.calls[0][1].orcaslicer_launcher_token).toBe("s3");
  });
});

describe("OrcaSlicerFrame", () => {
  it("shows the https banner off-https and the session strip with End session", async () => {
    mockGet({ sess: session });
    deleteMock.mockResolvedValue(undefined);
    wrap(<OrcaSlicerFrame />);
    expect(await screen.findByText("a.3mf")).toBeInTheDocument();
    // jsdom serves http://localhost: localhost is exempt, so no banner.
    expect(screen.queryByText(/needs HTTPS/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "End session" }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/orcaslicer/session"));
  });

  it("shows the banner on a non-local http host", async () => {
    mockGet({});
    const orig = window.location;
    Object.defineProperty(window, "location", { value: { ...orig, protocol: "http:", hostname: "tdmm.lan" }, configurable: true });
    try {
      wrap(<OrcaSlicerFrame />);
      expect(await screen.findByText(/needs HTTPS/)).toBeInTheDocument();
    } finally {
      Object.defineProperty(window, "location", { value: orig, configurable: true });
    }
  });

  it("waits for a file when there is no session", async () => {
    mockGet({});
    wrap(<OrcaSlicerFrame />);
    expect(await screen.findByTestId("orcaslicer-waiting")).toHaveTextContent("Waiting for a file");
    expect(screen.queryByTitle("OrcaSlicer")).toBeNull();
  });

  it("renders the iframe with a skeleton until load", async () => {
    mockGet({ sess: session });
    wrap(<OrcaSlicerFrame />);
    const frame = await screen.findByTitle("OrcaSlicer");
    expect(frame).toHaveAttribute("src", "/slicer/");
    expect(frame).toHaveAttribute("allow", "clipboard-read; clipboard-write; fullscreen");
    expect(screen.getByTestId("orcaslicer-skeleton")).toBeInTheDocument();
    fireEvent.load(frame);
    expect(screen.queryByTestId("orcaslicer-skeleton")).toBeNull();
  });
});

describe("Open in OrcaSlicer menu entry", () => {
  async function openMenu(f: FileOut, opts: Parameters<typeof mockGet>[0], isDoc = false) {
    mockGet(opts);
    wrap(<FileActionsMenu file={f} model={model} isDoc={isDoc} />);
    // let /features + /orcaslicer/session resolve
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/features"));
    await new Promise((r) => setTimeout(r, 20));
    fireEvent.pointerDown(screen.getByRole("button", { name: /Actions for/ }), { button: 0 });
  }

  it("is shown for an openable format when enabled", async () => {
    await openMenu(file(), { enabled: true });
    expect(await screen.findByRole("menuitem", { name: /in OrcaSlicer/ })).toBeInTheDocument();
  });

  it("is hidden when disabled, for unsupported formats, and for docs", async () => {
    await openMenu(file(), { enabled: false });
    await screen.findByRole("menuitem", { name: /Download/ });
    expect(screen.queryByRole("menuitem", { name: /in OrcaSlicer/ })).toBeNull();
  });

  it("is hidden for non-admins", async () => {
    await openMenu(file(), { enabled: true, admin: false });
    await screen.findByRole("menuitem", { name: /Download/ });
    expect(screen.queryByRole("menuitem", { name: /in OrcaSlicer/ })).toBeNull();
  });

  it("is hidden for gcode", async () => {
    await openMenu(file({ format: "gcode", kind: "gcode" }), { enabled: true });
    await screen.findByRole("menuitem", { name: /Download/ });
    expect(screen.queryByRole("menuitem", { name: /in OrcaSlicer/ })).toBeNull();
  });

  it("opens the window before the POST resolves, using the saved mode", async () => {
    localStorage.setItem("tdmm.orcaslicer.openMode", "window");
    let resolvePost: (v: unknown) => void = () => {};
    postMock.mockReturnValue(new Promise((r) => (resolvePost = r)));
    await openMenu(file(), { enabled: true });
    fireEvent.click(await screen.findByRole("menuitem", { name: /in OrcaSlicer/ }));
    expect(openMock).toHaveBeenCalledTimes(1);
    expect(openMock.mock.calls[0][0]).toBe("/slicer");
    expect(openMock.mock.calls[0][1]).toBe("tdmm-orcaslicer");
    expect(openMock.mock.calls[0][2]).toMatch(/^popup,width=\d+,height=\d+$/);
    resolvePost({ token: "t", iframe_path: "/slicer/", replaced_session: false, file: { id: 2, name: "b.stl" } });
    await waitFor(() => expect(toastMock.success).toHaveBeenCalled());
    expect(closeMock).not.toHaveBeenCalled();
  });

  it("opens a plain named tab by default", async () => {
    postMock.mockResolvedValue({ token: "t", iframe_path: "/slicer/", replaced_session: false, file: { id: 2, name: "b.stl" } });
    await openMenu(file(), { enabled: true });
    fireEvent.click(await screen.findByRole("menuitem", { name: /in OrcaSlicer/ }));
    expect(openMock).toHaveBeenCalledWith("/slicer", "tdmm-orcaslicer");
    expect(localStorage.getItem("tdmm.orcaslicer.openMode")).toBe("tab");
  });

  it("closes the window and toasts when the POST fails", async () => {
    postMock.mockRejectedValue(new ApiError(502, "bad gateway", "launcher_unreachable"));
    await openMenu(file(), { enabled: true });
    fireEvent.click(await screen.findByRole("menuitem", { name: /in OrcaSlicer/ }));
    expect(openMock).toHaveBeenCalled();
    await waitFor(() => expect(closeMock).toHaveBeenCalled());
    expect(toastMock.error).toHaveBeenCalled();
  });

  it("opens directly with no session", async () => {
    postMock.mockResolvedValue({ token: "t", iframe_path: "/slicer/", replaced_session: false, file: { id: 2, name: "b.stl" } });
    await openMenu(file(), { enabled: true });
    fireEvent.click(await screen.findByRole("menuitem", { name: /in OrcaSlicer/ }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/models/m/files/2/orcaslicer", {}));
    expect(openMock).toHaveBeenCalled();
    expect(toastMock.success).toHaveBeenCalledWith("Opening b.stl in OrcaSlicer");
  });

  it("confirms before replacing a session on a different file", async () => {
    postMock.mockResolvedValue({ token: "t", iframe_path: "/slicer/", replaced_session: true, file: { id: 2, name: "b.stl" } });
    await openMenu(file(), { enabled: true, sess: session });
    fireEvent.click(await screen.findByRole("menuitem", { name: /in OrcaSlicer/ }));
    expect(await screen.findByText(/unsaved work there is lost/)).toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalled();
    expect(openMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(openMock).toHaveBeenCalled();
    await waitFor(() => expect(postMock).toHaveBeenCalled());
  });

  it("confirms a relaunch of the same file while OrcaSlicer is running", async () => {
    await openMenu(file({ id: 1 }), { enabled: true, sess: session });
    fireEvent.click(await screen.findByRole("menuitem", { name: /in OrcaSlicer/ }));
    expect(await screen.findByText(/unsaved work there is lost/)).toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalled();
  });

  it("maps launcher_busy to a restarting toast", async () => {
    postMock.mockRejectedValue(new ApiError(409, "busy", "launcher_busy"));
    await openMenu(file(), { enabled: true });
    fireEvent.click(await screen.findByRole("menuitem", { name: /in OrcaSlicer/ }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith("OrcaSlicer is restarting, try again in a few seconds"));
  });

  it("maps launcher_unreachable to a toast", async () => {
    postMock.mockRejectedValue(new ApiError(502, "bad gateway", "launcher_unreachable"));
    await openMenu(file(), { enabled: true });
    fireEvent.click(await screen.findByRole("menuitem", { name: /in OrcaSlicer/ }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith("Cannot reach the OrcaSlicer launcher"));
  });
});
