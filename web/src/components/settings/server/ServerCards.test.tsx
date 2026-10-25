import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { serverSettingsQueryOptions, type ServerSettingsOut, type ServerStatusOut } from "@/api/serverSettings";
import { TooltipProvider } from "@/components/ui/tooltip";
import { fakeServer, fakeStatus } from "@/components/settings/server/fixtures";
import { LimitsCard } from "@/components/settings/server/LimitsCard";
import { ObicoServerCard } from "@/components/settings/server/ObicoServerCard";
import { PublicUrlCard } from "@/components/settings/server/PublicUrlCard";
import { ServerTab } from "@/components/settings/server/ServerTab";
import { ServicesCard } from "@/components/settings/server/ServicesCard";
import { SlicerServerCard } from "@/components/settings/server/SlicerServerCard";
import { VpNetworkCard } from "@/components/settings/server/VpNetworkCard";

const { getMock, postMock, putMock, toastMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  putMock: vi.fn(),
  toastMock: { success: vi.fn(), error: vi.fn() },
}));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock, put: putMock } };
});
vi.mock("sonner", () => ({ toast: toastMock }));

let status: ServerStatusOut;
const ADMIN_STATUS = {
  overall: "warn",
  checked_at: 1,
  services: {
    db: { ok: true, state: "ok", detail: "ok", version: "16.4", checked_at: 1 },
    beat: { ok: false, state: "down", detail: "no heartbeat recorded", version: null, checked_at: 1 },
    smtp: { ok: false, state: "off", detail: "not configured", version: null, checked_at: 1 },
  },
};
const OBICO = { url: "http://obico-ml:3333", enabled: false, interval_s: 10, warn: 0.38, pause: 0.78, action: "notify", callback_url: "http://api:8080" };

// Saves merge onto the cached config, so seed the cache with the card's data.
function ui(node: ReactElement, seed?: ServerSettingsOut) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (seed) qc.setQueryData(serverSettingsQueryOptions.queryKey, seed);
  invalidateSpy = vi.spyOn(qc, "invalidateQueries");
  return { qc, ...render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{node}</TooltipProvider>
    </QueryClientProvider>,
  ) };
}
let invalidateSpy: ReturnType<typeof vi.spyOn>;

let current: ServerSettingsOut;
beforeEach(() => {
  [getMock, postMock, putMock, toastMock.success, toastMock.error].forEach((m) => m.mockReset());
  status = fakeStatus();
  current = fakeServer();
  getMock.mockImplementation((path: string) => {
    if (path === "/settings/server/status") return Promise.resolve(status);
    if (path === "/settings/server") return Promise.resolve(current);
    if (path === "/settings/obico") return Promise.resolve(OBICO);
    if (path === "/admin/status") return Promise.resolve(ADMIN_STATUS);
    return Promise.resolve(null);
  });
  putMock.mockImplementation((path: string, body: unknown) =>
    Promise.resolve(path === "/settings/server" ? { ...current, config: body } : body),
  );
});

describe("PublicUrlCard", () => {
  it("flags a different port or host as a mismatch even when the origin is a prefix", () => {
    ui(<PublicUrlCard data={fakeServer()} />, fakeServer());
    const o = window.location.origin;
    fireEvent.change(screen.getByLabelText("Public URL"), { target: { value: `${o}9` } });
    expect(screen.getByRole("alert")).toHaveTextContent("differs from the address you are using");
    fireEvent.change(screen.getByLabelText("Public URL"), { target: { value: `${o}/sub` } });
    expect(screen.queryByText(/differs from the address/)).not.toBeInTheDocument();
  });

  it("shows the server-derived cookie state until edited", () => {
    ui(<PublicUrlCard data={fakeServer({}, { derived: { cookie_secure: true, request_origin: "x" } })} />, fakeServer());
    expect(screen.getByText(/will be Secure/)).toBeInTheDocument();
  });

  it("warns on origin mismatch, offers current origin, and confirms before saving", async () => {
    ui(<PublicUrlCard data={fakeServer()} />, fakeServer());
    const input = screen.getByLabelText("Public URL");
    fireEvent.change(input, { target: { value: "https://models.example.com/" } });
    expect(screen.getByRole("alert")).toHaveTextContent("differs from the address you are using");
    expect(screen.getByText(/will be Secure/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(putMock).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "Save anyway" }));
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("/settings/server", expect.objectContaining({ public_url: "https://models.example.com" })),
    );
  });

  it("Use current origin fills the origin and saves without a confirm", async () => {
    ui(<PublicUrlCard data={fakeServer()} />, fakeServer());
    fireEvent.click(screen.getByRole("button", { name: "Use current origin" }));
    expect(screen.getByLabelText("Public URL")).toHaveValue(window.location.origin);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(putMock).toHaveBeenCalled());
  });

  it("shows the seeded hint", () => {
    ui(<PublicUrlCard data={fakeServer({}, { seeded_from_env: { public_url: "TDMM_PUBLIC_URL" } })} />, fakeServer({}, { seeded_from_env: { public_url: "TDMM_PUBLIC_URL" } }));
    expect(screen.getByText("Seeded from environment (TDMM_PUBLIC_URL)")).toBeInTheDocument();
  });
});

describe("LimitsCard", () => {
  it("converts MB to bytes and saves the full object", async () => {
    ui(<LimitsCard data={fakeServer()} />, fakeServer());
    expect(screen.getByLabelText("Max upload (MB)")).toHaveValue(1024);
    fireEvent.change(screen.getByLabelText("Max upload (MB)"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Log level"), { target: { value: "DEBUG" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith(
        "/settings/server",
        expect.objectContaining({ max_upload_bytes: 2097152, log_level: "DEBUG", slicer_url: "http://slicer:3001" }),
      ),
    );
    await waitFor(() => expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["features"] }));
  });

  it("merges each card's patch onto the latest config, so concurrent saves don't clobber", async () => {
    const base = fakeServer();
    const { qc } = ui(
      <>
        <LimitsCard data={base} />
        <SlicerServerCard data={base} />
      </>,
      base,
    );
    fireEvent.change(screen.getByLabelText("Log level"), { target: { value: "DEBUG" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save" })[0]);
    await waitFor(() => expect(putMock).toHaveBeenCalledTimes(1));
    // putMock echoes the body into the cache, as the real API would.
    fireEvent.change(screen.getByLabelText("Slice timeout (seconds)"), { target: { value: "900" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save" })[1]);
    await waitFor(() => expect(putMock).toHaveBeenCalledTimes(2));
    expect(putMock.mock.calls[1][1]).toEqual(expect.objectContaining({ log_level: "DEBUG", slice_timeout_s: 900 }));
    expect(qc.getQueryData<ServerSettingsOut>(serverSettingsQueryOptions.queryKey)?.config.log_level).toBe("DEBUG");
  });

  it("rejects an out-of-range heartbeat", () => {
    ui(<LimitsCard data={fakeServer()} />, fakeServer());
    fireEvent.change(screen.getByLabelText("SSE heartbeat (seconds)"), { target: { value: "1" } });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });
});

describe("SlicerServerCard", () => {
  it("shows health from status and saves the toggle", async () => {
    ui(<SlicerServerCard data={fakeServer()} />, fakeServer());
    expect(await screen.findByText("Healthy")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Use the slicer"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("/settings/server", expect.objectContaining({ slicer_enabled: false })),
    );
  });

  it("shows Not checked when health is null", async () => {
    status = fakeStatus({ slicer_healthy: null });
    ui(<SlicerServerCard data={fakeServer({ slicer_enabled: false })} />, fakeServer({ slicer_enabled: false }));
    expect(await screen.findByText("Not checked")).toBeInTheDocument();
  });

  it("Test posts the draft URL and toasts the result", async () => {
    postMock.mockResolvedValue({ ok: true, latency_ms: 12, detail: "ok" });
    ui(<SlicerServerCard data={fakeServer()} />, fakeServer());
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith("Slicer reachable (12 ms)"));
    expect(postMock).toHaveBeenCalledWith("/settings/server/slicer/test", { url: "http://slicer:3001" });
  });

  it("toasts a failed test", async () => {
    postMock.mockResolvedValue({ ok: false, latency_ms: null, detail: "refused" });
    ui(<SlicerServerCard data={fakeServer()} />, fakeServer());
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith("Slicer check failed: refused"));
  });
});

describe("ObicoServerCard", () => {
  it("round-trips the full obico object", async () => {
    ui(<ObicoServerCard />);
    fireEvent.change(await screen.findByLabelText("ML API URL"), { target: { value: "http://ml:3333" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("/settings/obico", { ...OBICO, url: "http://ml:3333" }),
    );
  });

  it("edits enable, thresholds and default action, and blocks pause < warn", async () => {
    ui(<ObicoServerCard />);
    fireEvent.click(await screen.findByLabelText("Enable failure detection"));
    fireEvent.change(screen.getByLabelText("Default action"), { target: { value: "pause" } });
    fireEvent.change(screen.getByLabelText("Pause threshold"), { target: { value: "0.1" } });
    expect(screen.getByRole("alert")).toHaveTextContent("at least the warn threshold");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Pause threshold"), { target: { value: "0.6" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("/settings/obico", { ...OBICO, enabled: true, action: "pause", pause: 0.6 }),
    );
  });

  it("flags env-seeded values and shows the ML version", async () => {
    getMock.mockImplementation((path: string) =>
      Promise.resolve(
        path === "/settings/obico"
          ? { ...OBICO, seeded_from_env: { url: "TDMM_OBICO_URL" }, ml_version: "6f938964c63892df", ml_version_source: "configured" }
          : path === "/settings/server/status" ? status : null,
      ),
    );
    ui(<ObicoServerCard />);
    expect(await screen.findByText("Seeded from environment (TDMM_OBICO_URL)")).toBeInTheDocument();
    expect(screen.getByText(/6f938964c638/)).toBeInTheDocument();
  });

  it("reports the three connection checks separately", async () => {
    postMock.mockResolvedValue({
      ok: false,
      detail: "failed: callback, inference",
      ml_version: null,
      checks: [
        { name: "sidecar", ok: true, detail: "HTTP 200 in 12 ms" },
        { name: "callback", ok: false, detail: "sidecar did not fetch the frame; it cannot reach http://api:8080" },
        { name: "inference", ok: false, detail: "skipped: no frame fetched" },
      ],
    });
    ui(<ObicoServerCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    const list = await screen.findByRole("list", { name: "Connection checks" });
    expect(postMock).toHaveBeenCalledWith("/settings/obico/test", { url: "http://obico-ml:3333", callback_url: "http://api:8080" });
    const rows = list.querySelectorAll("li");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("Pass");
    expect(rows[0]).toHaveTextContent("HTTP 200 in 12 ms");
    expect(rows[1]).toHaveTextContent("Fail");
    expect(rows[1]).toHaveTextContent("cannot reach http://api:8080");
    expect(rows[2]).toHaveTextContent("skipped");
  });
});

describe("ServicesCard", () => {
  it("lists every service with its state", async () => {
    ui(<ServicesCard />);
    const beat = await screen.findByTestId("svc-beat");
    expect(beat).toHaveTextContent("Scheduler (beat)");
    expect(beat).toHaveTextContent("no heartbeat recorded");
    expect(beat.querySelector("[aria-label=Down]")).not.toBeNull();
    expect(screen.getByTestId("svc-db")).toHaveTextContent("16.4");
    expect(screen.getByTestId("svc-smtp").querySelector("[aria-label='Not in use']")).not.toBeNull();
  });
});

describe("VpNetworkCard", () => {
  it("blocks duplicate ports and a pasv range that overlaps a fixed port", () => {
    ui(<VpNetworkCard data={fakeServer()} />, fakeServer());
    fireEvent.change(screen.getByLabelText("MQTT port"), { target: { value: "2021" } });
    expect(screen.getByRole("alert")).toHaveTextContent("must be different");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("MQTT port"), { target: { value: "50005" } });
    expect(screen.getByRole("alert")).toHaveTextContent("must not include");
    fireEvent.change(screen.getByLabelText("MQTT port"), { target: { value: "8883" } });
    fireEvent.change(screen.getByLabelText("Passive port min"), { target: { value: "50020" } });
    expect(screen.getByRole("alert")).toHaveTextContent("must not exceed");
  });

  it("saves, mapping an empty passive address to null, and shows status errors", async () => {
    status = fakeStatus({
      virtual_printer: { state: "error", vp_id: 1, bind: null, ports: null, pasv: null, errors: ["port 990 in use"], ts: null },
    });
    ui(<VpNetworkCard data={fakeServer({ vp_pasv_address: "10.0.0.5" })} />, fakeServer({ vp_pasv_address: "10.0.0.5" }));
    expect(await screen.findByText("port 990 in use")).toBeInTheDocument();
    expect(screen.getByText("Error")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Passive address"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("/settings/server", expect.objectContaining({ vp_pasv_address: null })),
    );
  });
});

describe("ServerTab", () => {
  it("renders the ignored-env banner", async () => {
    current = fakeServer({}, { ignored_env: ["TDMM_SLICER_URL", "COOKIE_SECURE"] });
    ui(<ServerTab />);
    expect(await screen.findByText(/TDMM_SLICER_URL, COOKIE_SECURE/)).toBeInTheDocument();
    expect(screen.getByText("Public URL", { selector: "[data-slot=card-title], div, h3" })).toBeInTheDocument();
    expect(await screen.findByTestId("svc-db")).toBeInTheDocument();
  });
});
