import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "@/api/auth";
import type { PrinterOverviewItem, PrintersOverviewOut, PrinterStatusOut } from "@/api/types";
import { EventsProvider } from "@/hooks/useEvents";
import { PrintDashboardPage } from "@/pages/PrintDashboardPage";

// Mocks the whole fetch layer (`api.get/post/patch/delete`), the same
// posture `printers.test.ts` uses -- every hook on this page (and the
// rail widgets it composes) runs for real; only the network boundary is
// faked, so the SSE-driven refetch below exercises the actual
// invalidate-then-refetch wiring instead of a stand-in.
const { getMock, patchMock, meBox, featuresBox, overviewBox } = vi.hoisted(() => ({
  getMock: vi.fn(),
  patchMock: vi.fn(),
  meBox: {
    current: {
      id: 1,
      username: "tester",
      display_name: null,
      email: null,
      role: "admin",
      permissions: [],
      groups: [],
      has_password: true,
      printer_access: null,
    } as Me,
  },
  featuresBox: { current: { printer_enabled: true } },
  overviewBox: { current: { printers: [], up_next: [], recent_runs: [] } as PrintersOverviewOut },
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      get: (url: string) => {
        if (url === "/auth/me") return Promise.resolve(meBox.current);
        if (url === "/features") return Promise.resolve(featuresBox.current);
        if (url === "/printers/overview") return getMock(url);
        if (url === "/printers") return Promise.resolve([]);
        return Promise.reject(new Error(`unexpected GET ${url}`));
      },
      patch: (url: string, body: unknown) => patchMock(url, body),
      post: vi.fn(),
    },
  };
});

function status(overrides: Partial<PrinterStatusOut> = {}): PrinterStatusOut {
  return {
    online: true,
    gcode_state: "IDLE",
    mc_percent: null,
    layer_num: null,
    total_layer_num: null,
    mc_remaining_time: null,
    print_error: null,
    nozzle_temper: null,
    bed_temper: null,
    subtask_name: null,
    wifi_signal: null,
    trays: [],
    ...overrides,
  };
}

function printerItem(id: number, overrides: Partial<PrinterOverviewItem> = {}): PrinterOverviewItem {
  return {
    printer: {
      id,
      name: `Printer ${id}`,
      kind: "bambu_lan",
      host: "192.168.1.50",
      serial: `AC${id}`,
      model: "A1 mini",
      enabled: true,
      options: {},
      access_code_set: true,
      build_volume_mm: null,
    },
    status: status(),
    active_job: null,
    last_runs: [],
    can_print: true,
    can_control: true,
    ...overrides,
  };
}

function renderPage() {
  const rootRoute = createRootRoute();
  const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: PrintDashboardPage });
  const modelRoute = createRoute({ getParentRoute: () => rootRoute, path: "/models/$slug", component: () => null });
  const printerRoute = createRoute({ getParentRoute: () => rootRoute, path: "/printers/$id", component: () => null });
  const queueRoute = createRoute({ getParentRoute: () => rootRoute, path: "/print/queue", component: () => null });
  const router = createRouter({
    routeTree: rootRoute.addChildren([indexRoute, modelRoute, printerRoute, queueRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <EventsProvider>
        <RouterProvider router={router} />
      </EventsProvider>
    </QueryClientProvider>,
  );
}

// jsdom has no `EventSource` -- `EventsProvider` (mounted by every render
// here, same as the real app shell) needs a stub to open against even when
// a test doesn't care about SSE itself (mirrors `useEvents.test.tsx`'s
// `FakeEventSource`).
class StubEventSource {
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  readonly url: string;
  constructor(url: string) {
    this.url = url;
  }
  close() {}
}

beforeEach(() => {
  getMock.mockReset();
  patchMock.mockReset();
  featuresBox.current = { printer_enabled: true };
  overviewBox.current = { printers: [], up_next: [], recent_runs: [] };
  getMock.mockImplementation(() => Promise.resolve(overviewBox.current));
  vi.stubGlobal("EventSource", StubEventSource);
});

describe("PrintDashboardPage overview error", () => {
  // Fix-review finding 4: an errored overview poll used to fall through to
  // the "0 printers" branch and render the setup card, hiding the real
  // failure. It must show a retry-able error state instead.
  it("shows an error state with a retry button, never the setup card", async () => {
    featuresBox.current = { printer_enabled: true };
    getMock.mockImplementation(() => Promise.reject(new Error("boom")));

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load the print overview.");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText("Printer setup")).not.toBeInTheDocument();
  });
});

describe("PrintDashboardPage printer counts", () => {
  it("shows the PrinterSetupCard with 0 printers", async () => {
    overviewBox.current = { printers: [], up_next: [], recent_runs: [] };
    featuresBox.current = { printer_enabled: false };

    renderPage();

    expect(await screen.findByText("Printer setup")).toBeInTheDocument();
  });

  it("renders 1 card without a 2-column grid with 1 printer", async () => {
    overviewBox.current = { printers: [printerItem(1)], up_next: [], recent_runs: [] };

    renderPage();

    await screen.findByText("Printer 1");
    expect(document.querySelectorAll('[data-slot="printer-card"]')).toHaveLength(1);
    expect(document.querySelector(".md\\:grid-cols-2")).toBeNull();
  });

  it("renders 2 cards with a md:grid-cols-2 grid with 2 printers", async () => {
    overviewBox.current = {
      printers: [
        printerItem(1, {
          status: status({ gcode_state: "RUNNING", mc_percent: 42, layer_num: 10, total_layer_num: 120 }),
        }),
        printerItem(2, { status: status({ gcode_state: "IDLE" }) }),
      ],
      up_next: [],
      recent_runs: [],
    };

    renderPage();

    await screen.findByText("Printer 1");
    expect(document.querySelectorAll('[data-slot="printer-card"]')).toHaveLength(2);
    expect(document.querySelector(".md\\:grid-cols-2")).not.toBeNull();
  });
});

describe("PrintDashboardPage SSE refresh", () => {
  class FakeEventSource {
    static instances: FakeEventSource[] = [];
    onmessage: ((event: MessageEvent<string>) => void) | null = null;
    onerror: (() => void) | null = null;
    readonly url: string;
    constructor(url: string) {
      this.url = url;
      FakeEventSource.instances.push(this);
    }
    close() {}
  }

  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  const ACTIVE_JOB = {
    id: 1,
    printer_id: 1,
    file_id: 10,
    origin: "app" as const,
    subtask_name: "dragon.gcode.3mf",
    state: "printing" as const,
    progress_pct: 42,
    remaining_min: 65,
    layer: 10,
    total_layers: 120,
    printer_error: null,
    created_at: "2026-10-22T10:00:00Z",
    started_at: "2026-10-22T10:00:00Z",
    finished_at: null,
    plate: 1,
    model_id: 7,
    model_slug: "dragon",
    model_name: "Dragon",
  };

  it("picks up a print_job.updated event and shows the refreshed percent", async () => {
    overviewBox.current = {
      printers: [
        printerItem(1, {
          active_job: ACTIVE_JOB,
          status: status({ gcode_state: "RUNNING", mc_percent: 42 }),
        }),
      ],
      up_next: [],
      recent_runs: [],
    };

    renderPage();
    await screen.findByText("Printer 1");

    // Flip the canned response BEFORE the invalidated refetch fires.
    overviewBox.current = {
      printers: [
        printerItem(1, {
          active_job: ACTIVE_JOB,
          status: status({ gcode_state: "RUNNING", mc_percent: 80 }),
        }),
      ],
      up_next: [],
      recent_runs: [],
    };

    const source = FakeEventSource.instances[0];
    source?.onmessage?.({
      data: JSON.stringify({ type: "print_job.updated", print_job_id: 1, printer_id: 1, state: "printing" }),
    } as MessageEvent<string>);

    await waitFor(() => expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "80"));
  });
});

describe("PrintDashboardPage recent runs grading", () => {
  it("clicking Good PATCHes /prints/{id} with quality good", async () => {
    overviewBox.current = {
      printers: [printerItem(1)],
      up_next: [],
      recent_runs: [
        {
          id: 9,
          print_job_id: 10,
          printer_id: 1,
          printer_name: "Printer 1",
          origin: "app",
          subtask_name: null,
          model_id: 7,
          model_slug: "dragon",
          model_name: "Dragon",
          revision_id: 2,
          result: "completed",
          duration_s: 60,
          est_filament: null,
          plate: 1,
          fetch_state: "done",
          fetch_error: null,
          has_thumbnail: false,
          has_file: true,
          started_at: null,
          finished_at: null,
          created_at: "2026-10-22T10:00:00Z",
          created_by_user_id: 1,
          run: {
            id: 77,
            model_id: 7,
            print_job_id: 10,
            source: "printer",
            quality: null,
            failure_reason: null,
            filament_cost: null,
            machine_cost: null,
            energy_kwh: null,
            energy_cost: null,
            total_cost: 2,
            created_by_user_id: 1,
            printed_at: "2026-10-22T10:00:00Z",
            printer_name: "Printer 1",
            filament: null,
            filament_g: 10,
            result: "success",
            duration_min: 1,
            notes: null,
            created_at: "2026-10-22T10:00:00Z",
            material_id: null,
            material: null,
            model_slug: null,
            model_name: null,
          },
        },
      ],
    };

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Good" }));

    await waitFor(() => expect(patchMock).toHaveBeenCalledExactlyOnceWith("/prints/77", { quality: "good" }));
  });
});

describe("PrintDashboardPage queue reorder", () => {
  it("moving a queued entry up PATCHes its new position", async () => {
    overviewBox.current = {
      printers: [printerItem(1)],
      up_next: [
        {
          id: 3,
          model_id: 5,
          position: 2,
          added_at: "2026-10-22T10:00:00Z",
          model: {
            id: 5,
            slug: "second-model",
            name: "Second Model",
            description: null,
            tags: [],
            updated_at: "2026-10-22T10:00:00Z",
            created_at: "2026-10-22T10:00:00Z",
            file_count: 1,
            formats: ["stl"],
            cover: null,
            render_url: null,
            print_time_s: null,
            has_sliced: false,
            source_site: null,
            source_collection_id: null,
            source_collection_title: null,
            favorite: false,
            dims_mm: null,
            best_slicer_file: null,
            printable_file: null,
          },
          printable_file: null,
        },
      ],
      recent_runs: [],
    };

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Move Second Model up" }));

    await waitFor(() => expect(patchMock).toHaveBeenCalledExactlyOnceWith("/queue/3", { position: 1 }));
  });
});
