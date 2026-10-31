import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Features } from "@/api/types";
import { SlicerIntegrationCard } from "@/components/settings/SlicerIntegrationCard";

// `vi.mock` factories are hoisted above the module's own top-level bindings
// (same pattern as BrowserExtensionCard.test.tsx), so the fakes have to be
// created through `vi.hoisted`.
const { getMock, postMock, deleteMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  deleteMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock, post: postMock, delete: deleteMock },
  };
});

const writeText = vi.fn().mockResolvedValue(undefined);

function featuresOut(overrides: Partial<Features> = {}): Features {
  return { printer_enabled: false, watch_dir: null, watch_enabled: false, ...overrides };
}

function mockGet(features: Features, tokens: unknown[] = []) {
  getMock.mockImplementation((path: string) => {
    if (path === "/features") return Promise.resolve(features);
    if (path === "/settings/api-tokens") return Promise.resolve(tokens);
    return Promise.resolve([]);
  });
}

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <SlicerIntegrationCard />
    </QueryClientProvider>,
  );
}

describe("SlicerIntegrationCard", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    deleteMock.mockReset();
    writeText.mockClear();
    Object.assign(navigator, { clipboard: { writeText } });
  });

  it("shows the empty token state and a mint form pre-filled with the suggested label", async () => {
    mockGet(featuresOut());

    renderCard();

    expect(await screen.findByText("No tokens yet.")).toBeInTheDocument();
    expect(screen.getByLabelText("Token label")).toHaveValue("OrcaSlicer");
    expect(
      screen.getByText("Uses the same API tokens as the browser extension — a token from either works for both."),
    ).toBeInTheDocument();
  });

  it("lists existing tokens", async () => {
    mockGet(featuresOut(), [
      { id: 1, label: "OrcaSlicer", created_at: "2026-07-01T00:00:00Z", last_used_at: null },
    ]);

    renderCard();

    expect(await screen.findByText("OrcaSlicer")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revoke" })).toBeInTheDocument();
  });

  it("mints a token from the pre-filled label and reveals the plaintext once", async () => {
    mockGet(featuresOut());
    postMock.mockResolvedValue({
      id: 9,
      label: "OrcaSlicer",
      token: "tdmm_secret_plaintext",
      created_at: "2026-07-10T00:00:00Z",
    });

    renderCard();
    await screen.findByText("No tokens yet.");

    fireEvent.click(screen.getByRole("button", { name: "Create token" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/settings/api-tokens", { label: "OrcaSlicer" }),
    );
    expect(await screen.findByText("tdmm_secret_plaintext")).toBeInTheDocument();
    expect(screen.getByText(/Set it as/)).toHaveTextContent("INTAKE_TOKEN");
  });

  it("shows the setup copy for both the post-processing script and the watched-folder loops", async () => {
    mockGet(featuresOut());

    renderCard();
    await screen.findByText("No tokens yet.");

    expect(screen.getByText("1. Auto-upload every slice (metadata)")).toBeInTheDocument();
    expect(screen.getByText(/Process → Others → Post-processing scripts/)).toBeInTheDocument();
    expect(screen.getByText("python3 /path/to/slicer_postprocess.py")).toBeInTheDocument();
    expect(screen.getByText("INTAKE_URL")).toBeInTheDocument();
    expect(screen.getByText("INTAKE_TOKEN")).toBeInTheDocument();
    expect(screen.getByText(/This path uploads plain \.gcode/)).toBeInTheDocument();
    expect(screen.getByText("2. Printable file (watched folder)")).toBeInTheDocument();
    expect(screen.getByText(/Export plate sliced file/)).toBeInTheDocument();
    expect(screen.getByText(/Send-to-printer button/)).toBeInTheDocument();
  });

  it("renders a cache-busting download link for the post-processing script", async () => {
    mockGet(featuresOut());

    renderCard();
    await screen.findByText("No tokens yet.");

    const link = screen.getByRole("link", { name: /Download slicer_postprocess\.py/ });
    expect(link).toHaveAttribute("href", expect.stringMatching(/^\/slicer_postprocess\.py\?v=\d+$/));
    expect(link).toHaveAttribute("download", "slicer_postprocess.py");
  });

  it("shows the watched-folder path and 'active' when the feature is enabled", async () => {
    mockGet(featuresOut({ watch_dir: "/watch", watch_enabled: true }));

    renderCard();

    expect(await screen.findByText(/Watched folder:/)).toBeInTheDocument();
    expect(screen.getByText("/watch")).toBeInTheDocument();
    expect(screen.getByText(/\(active\)/)).toBeInTheDocument();
    expect(screen.getByText(/WATCH_HOST_DIR/)).toBeInTheDocument();
  });

  it("shows a not-configured message when the watched folder is off", async () => {
    mockGet(featuresOut());

    renderCard();
    await screen.findByText("No tokens yet.");

    expect(screen.getByText(/Watched-folder polling is off/)).toBeInTheDocument();
    expect(screen.queryByText(/Watched folder:/)).not.toBeInTheDocument();
  });

  it("offers profile sync only when the sidecar is configured", async () => {
    getMock.mockImplementation((path: string) => {
      if (path === "/features") return Promise.resolve(featuresOut());
      if (path === "/slicer/health") return Promise.resolve({ configured: true, ok: true, version: "v1" });
      return Promise.resolve([]);
    });
    postMock.mockResolvedValue({ created: 3, updated: 0, skipped: 0 });

    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Sync Bambu system profiles" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/profiles/import", { from_sidecar: true, all: false }),
    );
    expect(await screen.findByText(/3 new, 0 updated/)).toBeInTheDocument();
  });

  it("hides profile sync when no sidecar is configured", async () => {
    mockGet(featuresOut());
    renderCard();
    await screen.findByText("No tokens yet.");
    expect(screen.queryByRole("button", { name: "Sync Bambu system profiles" })).toBeNull();
  });
});
