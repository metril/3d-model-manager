import { QueryClient } from "@tanstack/react-query";
import { createMemoryHistory, createRouter } from "@tanstack/react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock } };
});

import type { Me } from "@/api/auth";
import type { Features, PrinterOut } from "@/api/types";
import { printerRedirect, redirectForIndex, routeTree } from "@/routes";

function fakeMe(overrides: Partial<Me> = {}): Me {
  return {
    id: 1,
    username: "tester",
    display_name: null,
    email: null,
    role: "member",
    permissions: [],
    groups: [],
    has_password: true,
    printer_access: null,
    ...overrides,
  };
}

function fakePrinter(overrides: Partial<PrinterOut> = {}): PrinterOut {
  return {
    id: 7,
    name: "Bambu",
    kind: "bambu_lan",
    host: "10.0.0.5",
    serial: "ABC123",
    model: null,
    enabled: true,
    options: {},
    access_code_set: true,
    build_volume_mm: null,
    ...overrides,
  };
}

function fakeFeatures(overrides: Partial<Features> = {}): Features {
  return { printer_enabled: false, watch_dir: null, watch_enabled: false, ...overrides };
}

function mockApi({
  me,
  features,
  printers,
}: {
  me: Me;
  features: Features;
  printers: PrinterOut[];
}) {
  getMock.mockImplementation((path: string) => {
    if (path === "/auth/me") return Promise.resolve(me);
    if (path === "/features") return Promise.resolve(features);
    if (path === "/printers") return Promise.resolve(printers);
    return Promise.resolve([]);
  });
}

function makeRouter(entry: string) {
  return createRouter({
    routeTree,
    context: { queryClient: new QueryClient({ defaultOptions: { queries: { retry: false } } }) },
    history: createMemoryHistory({ initialEntries: [entry] }),
  });
}

describe("app routes", () => {
  beforeEach(() => {
    getMock.mockReset();
  });

  it("redirects / to /library when the printer flag is off", async () => {
    mockApi({ me: fakeMe(), features: fakeFeatures({ printer_enabled: false }), printers: [] });
    const router = makeRouter("/");
    await router.load();
    expect(router.state.location.pathname).toBe("/library");
  });

  it("redirects a member with no visible printers to /library", async () => {
    mockApi({ me: fakeMe({ role: "member", permissions: [] }), features: fakeFeatures({ printer_enabled: true }), printers: [] });
    const router = makeRouter("/");
    await router.load();
    expect(router.state.location.pathname).toBe("/library");
  });

  it("keeps an admin with no printers on /", async () => {
    mockApi({ me: fakeMe({ role: "admin" }), features: fakeFeatures({ printer_enabled: true }), printers: [] });
    const router = makeRouter("/");
    await router.load();
    expect(router.state.location.pathname).toBe("/");
  });

  it("keeps / when exactly one printer is visible", async () => {
    mockApi({ me: fakeMe(), features: fakeFeatures({ printer_enabled: true }), printers: [fakePrinter()] });
    const router = makeRouter("/");
    await router.load();
    expect(router.state.location.pathname).toBe("/");
  });

  it("preserves library search params when the flag-off redirect fires", async () => {
    mockApi({ me: fakeMe(), features: fakeFeatures({ printer_enabled: false }), printers: [] });
    const router = makeRouter("/?collection=3");
    await router.load();
    expect(router.state.location.pathname).toBe("/library");
    expect(router.state.location.search).toMatchObject({ collection: 3 });
  });

  it("redirects /dashboard to /print/history?tab=stats", async () => {
    mockApi({ me: fakeMe(), features: fakeFeatures({ printer_enabled: true }), printers: [] });
    const router = makeRouter("/dashboard");
    await router.load();
    expect(router.state.location.pathname).toBe("/print/history");
    expect(router.state.location.search).toMatchObject({ tab: "stats" });
  });

  it("redirects /queue to /print/queue", async () => {
    mockApi({ me: fakeMe(), features: fakeFeatures({ printer_enabled: true }), printers: [] });
    const router = makeRouter("/queue");
    await router.load();
    expect(router.state.location.pathname).toBe("/print/queue");
  });

  it("redirects /jobs to /system/jobs", async () => {
    mockApi({ me: fakeMe(), features: fakeFeatures({ printer_enabled: true }), printers: [] });
    const router = makeRouter("/jobs");
    await router.load();
    expect(router.state.location.pathname).toBe("/system/jobs");
  });

  it("redirects /printer to the sole printer's detail page", async () => {
    mockApi({ me: fakeMe(), features: fakeFeatures({ printer_enabled: true }), printers: [fakePrinter({ id: 7 })] });
    const router = makeRouter("/printer");
    await router.load();
    expect(router.state.location.pathname).toBe("/printers/7");
  });

  it("redirects /printer to / when there are two printers", async () => {
    mockApi({
      me: fakeMe(),
      features: fakeFeatures({ printer_enabled: true }),
      printers: [fakePrinter({ id: 7 }), fakePrinter({ id: 8 })],
    });
    const router = makeRouter("/printer");
    await router.load();
    expect(router.state.location.pathname).toBe("/");
  });
});

describe("redirectForIndex", () => {
  it("sends to /library when the flag is off, keeping search", () => {
    expect(
      redirectForIndex({
        features: fakeFeatures({ printer_enabled: false }),
        printers: [],
        me: fakeMe(),
        search: { collection: 3 },
      }),
    ).toEqual({ to: "/library", search: { collection: 3 } });
  });

  it("sends a member with no printers and no settings:write to /library", () => {
    expect(
      redirectForIndex({
        features: fakeFeatures({ printer_enabled: true }),
        printers: [],
        me: fakeMe({ role: "member", permissions: [] }),
        search: {},
      }),
    ).toEqual({ to: "/library", search: {} });
  });

  it("keeps an admin with no printers on / (settings:write via role)", () => {
    expect(
      redirectForIndex({
        features: fakeFeatures({ printer_enabled: true }),
        printers: [],
        me: fakeMe({ role: "admin" }),
        search: {},
      }),
    ).toBeNull();
  });

  it("keeps / when at least one printer is visible", () => {
    expect(
      redirectForIndex({
        features: fakeFeatures({ printer_enabled: true }),
        printers: [fakePrinter()],
        me: fakeMe(),
        search: {},
      }),
    ).toBeNull();
  });
});

describe("printerRedirect", () => {
  it("goes to the printer's detail page when exactly one is visible", () => {
    expect(printerRedirect([fakePrinter({ id: 7 })])).toEqual({ to: "/printers/$id", params: { id: "7" } });
  });

  it("falls back to / for zero or multiple printers", () => {
    expect(printerRedirect([])).toEqual({ to: "/" });
    expect(printerRedirect([fakePrinter({ id: 1 }), fakePrinter({ id: 2 })])).toEqual({ to: "/" });
  });
});
