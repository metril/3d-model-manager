import { createElement, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  cameraTokenNeedsRefresh,
  overviewRefetchInterval,
  statusRefetchInterval,
  usePrintersOverview,
} from "@/api/printers";
import type { CameraUrlsOut, PrintersOverviewOut } from "@/api/types";

// `vi.mock` factories are hoisted above the module's own top-level bindings
// (same pattern as jobs.test.tsx), so the fake has to be created through
// `vi.hoisted`.
const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock },
  };
});

function wrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

function fakeOverview(overrides: Partial<PrintersOverviewOut> = {}): PrintersOverviewOut {
  return { printers: [], up_next: [], recent_runs: [], ...overrides };
}

describe("statusRefetchInterval (usePrinterStatus poll cadence, Task 11)", () => {
  it("polls fast (a positive interval) while the printer is actively printing", () => {
    for (const state of ["RUNNING", "PREPARE", "PAUSE"]) {
      const interval = statusRefetchInterval(state);
      expect(typeof interval).toBe("number");
      expect(interval).toBeGreaterThan(0);
    }
  });

  it("stops polling (false) once gcode_state is confirmed-terminal", () => {
    for (const state of ["FINISH", "FAILED", "IDLE"]) {
      expect(statusRefetchInterval(state)).toBe(false);
    }
  });

  it("keeps a slow heartbeat for an unknown/empty/transient state rather than stopping", () => {
    expect(statusRefetchInterval("UNKNOWN")).toBe(8000);
    expect(statusRefetchInterval("")).toBe(8000);
    expect(statusRefetchInterval(undefined)).toBe(8000);
  });
});

describe("overviewRefetchInterval (usePrintersOverview poll cadence, Task 3)", () => {
  it("polls fast (2500ms) while any printer is actively printing/preparing/paused", () => {
    const data = fakeOverview({
      printers: [
        {
          printer: {} as never,
          status: { online: true, gcode_state: "RUNNING" } as never,
          active_job: null,
          last_runs: [],
          can_print: true,
          can_control: true,
        },
      ],
    });
    expect(overviewRefetchInterval(data)).toBe(2500);
  });

  it("backs off to a slow heartbeat (8000ms) when nothing is actively printing", () => {
    const data = fakeOverview({
      printers: [
        {
          printer: {} as never,
          status: { online: true, gcode_state: "IDLE" } as never,
          active_job: null,
          last_runs: [],
          can_print: true,
          can_control: true,
        },
      ],
    });
    expect(overviewRefetchInterval(data)).toBe(8000);
  });

  it("defaults to the slow heartbeat for undefined data", () => {
    expect(overviewRefetchInterval(undefined)).toBe(8000);
  });
});

describe("usePrintersOverview", () => {
  beforeEach(() => {
    getMock.mockReset();
  });

  it("GETs /printers/overview", async () => {
    getMock.mockResolvedValue(fakeOverview());
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { result } = renderHook(() => usePrintersOverview(), { wrapper: wrapper(queryClient) });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(getMock).toHaveBeenCalledWith("/printers/overview");
  });
});

function fakeCameraUrls(expiresInMs: number): CameraUrlsOut {
  return {
    stream_url: "/api/printers/1/camera/stream?token=abc",
    snapshot_url: "/api/printers/1/camera/snapshot?token=abc",
    expires_at: new Date(Date.now() + expiresInMs).toISOString(),
  };
}

describe("cameraTokenNeedsRefresh (camera token TTL margin, final review item 4)", () => {
  it("is false for undefined data", () => {
    expect(cameraTokenNeedsRefresh(undefined)).toBe(false);
  });

  it("is false well before expiry", () => {
    expect(cameraTokenNeedsRefresh(fakeCameraUrls(5 * 60_000))).toBe(false);
  });

  it("is true once within the 60s expiry margin", () => {
    expect(cameraTokenNeedsRefresh(fakeCameraUrls(30_000))).toBe(true);
  });

  it("is true once already expired", () => {
    expect(cameraTokenNeedsRefresh(fakeCameraUrls(-1_000))).toBe(true);
  });
});
