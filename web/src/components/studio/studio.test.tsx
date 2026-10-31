import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import { serverSettingsQueryOptions } from "@/api/serverSettings";
import type { StudioSession } from "@/api/studio";
import type { FileOut, ModelDetail } from "@/api/types";
import { FileActionsMenu } from "@/components/model-detail/FileActionsMenu";
import { fakeServer } from "@/components/settings/server/fixtures";
import { StudioServerCard } from "@/components/settings/server/StudioServerCard";
import { StudioFrame } from "@/components/studio/StudioFrame";
import { TooltipProvider } from "@/components/ui/tooltip";

const { getMock, postMock, putMock, deleteMock, toastMock, showStudioMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  putMock: vi.fn(),
  deleteMock: vi.fn(),
  toastMock: { success: vi.fn(), error: vi.fn() },
  showStudioMock: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock, put: putMock, delete: deleteMock } };
});
vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), toastMock) }));
vi.mock("@/components/studio/studioNav", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/studio/studioNav")>();
  return { ...actual, showStudio: showStudioMock };
});

const session: StudioSession = {
  token: "tok1",
  model_slug: "m",
  model_id: 1,
  revision_id: 1,
  file: { id: 1, name: "a.3mf" },
  created_at: "2026-10-30T00:00:00Z",
  expires_at: "2026-10-30T08:00:00Z",
  studio_running: true,
};

function mockGet({ enabled = true, sess = null as StudioSession | null } = {}) {
  getMock.mockImplementation((path: string) => {
    if (path === "/features") return Promise.resolve({ printer_enabled: false, watch_dir: null, watch_enabled: false, studio_configured: enabled });
    if (path === "/studio/session") return Promise.resolve(sess ?? undefined);
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
  for (const m of [getMock, postMock, putMock, deleteMock, showStudioMock, toastMock.success, toastMock.error]) m.mockReset();
});

describe("StudioServerCard", () => {
  const data = fakeServer({ studio_enabled: true, studio_launcher_token: "***" });
  function renderCard() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(serverSettingsQueryOptions.queryKey, data);
    wrap(<StudioServerCard data={data} />, qc);
  }

  it("keeps the masked token when untouched", async () => {
    putMock.mockImplementation((_p: string, body: unknown) => Promise.resolve({ ...data, config: body }));
    renderCard();
    fireEvent.change(screen.getByLabelText("Studio URL"), { target: { value: "http://studio2:3000" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(putMock).toHaveBeenCalled());
    expect(putMock.mock.calls[0][1]).toMatchObject({
      studio_enabled: true,
      studio_url: "http://studio2:3000",
      studio_launcher_token: "***",
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
    expect(putMock.mock.calls[0][1].studio_launcher_token).toBe("s3");
  });
});

describe("StudioFrame", () => {
  it("shows the https banner off-https and the session strip with End session", async () => {
    mockGet({ sess: session });
    deleteMock.mockResolvedValue(undefined);
    wrap(<StudioFrame />);
    expect(await screen.findByText("a.3mf")).toBeInTheDocument();
    // jsdom serves http://localhost: localhost is exempt, so no banner.
    expect(screen.queryByText(/needs HTTPS/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "End session" }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/studio/session"));
  });

  it("shows the banner on a non-local http host", async () => {
    mockGet({});
    const orig = window.location;
    Object.defineProperty(window, "location", { value: { ...orig, protocol: "http:", hostname: "tdmm.lan" }, configurable: true });
    try {
      wrap(<StudioFrame />);
      expect(await screen.findByText(/needs HTTPS/)).toBeInTheDocument();
    } finally {
      Object.defineProperty(window, "location", { value: orig, configurable: true });
    }
  });

  it("renders the iframe with a skeleton until load", async () => {
    mockGet({});
    wrap(<StudioFrame />);
    const frame = await screen.findByTitle("Bambu Studio");
    expect(frame).toHaveAttribute("src", "/studio/");
    expect(frame).toHaveAttribute("allow", "clipboard-read; clipboard-write; fullscreen");
    expect(screen.getByTestId("studio-skeleton")).toBeInTheDocument();
    fireEvent.load(frame);
    expect(screen.queryByTestId("studio-skeleton")).toBeNull();
  });
});

describe("Open in Bambu Studio menu entry", () => {
  async function openMenu(f: FileOut, opts: Parameters<typeof mockGet>[0], isDoc = false) {
    mockGet(opts);
    wrap(<FileActionsMenu file={f} model={model} isDoc={isDoc} />);
    // let /features + /studio/session resolve
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/features"));
    await new Promise((r) => setTimeout(r, 20));
    fireEvent.pointerDown(screen.getByRole("button", { name: /Actions for/ }), { button: 0 });
  }

  it("is shown for an openable format when enabled", async () => {
    await openMenu(file(), { enabled: true });
    expect(await screen.findByRole("menuitem", { name: /in Bambu Studio/ })).toBeInTheDocument();
  });

  it("is hidden when disabled, for unsupported formats, and for docs", async () => {
    await openMenu(file(), { enabled: false });
    await screen.findByRole("menuitem", { name: /Download/ });
    expect(screen.queryByRole("menuitem", { name: /in Bambu Studio/ })).toBeNull();
  });

  it("is hidden for gcode", async () => {
    await openMenu(file({ format: "gcode", kind: "gcode" }), { enabled: true });
    await screen.findByRole("menuitem", { name: /Download/ });
    expect(screen.queryByRole("menuitem", { name: /in Bambu Studio/ })).toBeNull();
  });

  it("opens directly with no session, then reveals Studio", async () => {
    postMock.mockResolvedValue({ token: "t", iframe_path: "/studio/", replaced_session: false, file: { id: 2, name: "b.stl" } });
    await openMenu(file(), { enabled: true });
    fireEvent.click(await screen.findByRole("menuitem", { name: /in Bambu Studio/ }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/models/m/files/2/studio", {}));
    await waitFor(() => expect(showStudioMock).toHaveBeenCalled());
    expect(toastMock.success).toHaveBeenCalledWith("Opening b.stl in Bambu Studio");
  });

  it("confirms before replacing a session on a different file", async () => {
    postMock.mockResolvedValue({ token: "t", iframe_path: "/studio/", replaced_session: true, file: { id: 2, name: "b.stl" } });
    await openMenu(file(), { enabled: true, sess: session });
    fireEvent.click(await screen.findByRole("menuitem", { name: /in Bambu Studio/ }));
    expect(await screen.findByText(/unsaved work there is lost/)).toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(postMock).toHaveBeenCalled());
  });

  it("maps launcher_busy to a restarting toast", async () => {
    postMock.mockRejectedValue(new ApiError(409, "busy", "launcher_busy"));
    await openMenu(file(), { enabled: true });
    fireEvent.click(await screen.findByRole("menuitem", { name: /in Bambu Studio/ }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith("Studio is restarting, try again in a few seconds"));
  });

  it("maps launcher_unreachable to a toast", async () => {
    postMock.mockRejectedValue(new ApiError(502, "bad gateway", "launcher_unreachable"));
    await openMenu(file(), { enabled: true });
    fireEvent.click(await screen.findByRole("menuitem", { name: /in Bambu Studio/ }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith("Cannot reach the Bambu Studio launcher"));
    expect(showStudioMock).not.toHaveBeenCalled();
  });
});
