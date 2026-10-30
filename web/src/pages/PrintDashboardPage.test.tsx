import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
const { getMock, patchMock, postMock, meBox, featuresBox, overviewBox, reviewCountBox, reviewInboxBox } = vi.hoisted(() => ({
  getMock: vi.fn(),
  patchMock: vi.fn(),
  postMock: vi.fn(),
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
  reviewCountBox: { current: { count: 0 } },
  reviewInboxBox: { current: [] as unknown[] },
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
        if (url === "/queue/review-inbox/count") return Promise.resolve(reviewCountBox.current);
        if (url === "/queue/review-inbox") return Promise.resolve(reviewInboxBox.current);
        return Promise.reject(new Error(`unexpected GET ${url}`));
      },
      patch: (url: string, body: unknown) => patchMock(url, body),
      post: (url: string, body?: unknown) =>
        url.includes("/camera/token")
          ? Promise.resolve({ stream_url: "/stream", snapshot_url: "/snapshot", expires_at: "2026-01-01T00:00:00Z" })
          : postMock(url, body) ?? Promise.resolve(undefined),
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
    up_next: [],
    ...overrides,
  };
}

function renderPage(initial = "/") {
  const rootRoute = createRootRoute();
  const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: PrintDashboardPage });
  const modelRoute = createRoute({ getParentRoute: () => rootRoute, path: "/models/$slug", component: () => null });
  const printerRoute = createRoute({ getParentRoute: () => rootRoute, path: "/printers/$id", component: () => null });
  const queueRoute = createRoute({ getParentRoute: () => rootRoute, path: "/print/queue", component: () => null });
  const router = createRouter({
    routeTree: rootRoute.addChildren([indexRoute, modelRoute, printerRoute, queueRoute]),
    history: createMemoryHistory({ initialEntries: [initial] }),
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
  postMock.mockReset();
  localStorage.clear();
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

  it("renders a card per printer in an auto-fill grid (default M: minmax 340px for 3+ printers)", async () => {
    overviewBox.current = {
      printers: [printerItem(1), printerItem(2, { status: status({ gcode_state: "IDLE" }) }), printerItem(3)],
      up_next: [],
      recent_runs: [],
    };

    renderPage();

    await screen.findByText("Printer 1");
    expect(document.querySelectorAll('[data-slot="printer-card"]')).toHaveLength(3);
    expect(document.querySelector('[data-slot="printer-card"]')).toHaveAttribute("data-size", "M");
    const grid = document.querySelector('[data-slot="printer-card"]')!.parentElement as HTMLElement;
    expect(grid.style.gridTemplateColumns).toContain("minmax(min(340px");
  });

  it("defaults to size L with 2 printers or fewer; a stored choice wins", async () => {
    overviewBox.current = { printers: [printerItem(1), printerItem(2)], up_next: [], recent_runs: [] };
    const { unmount } = renderPage();
    await screen.findByText("Printer 1");
    expect(document.querySelector('[data-slot="printer-card"]')).toHaveAttribute("data-size", "L");
    unmount();
    localStorage.setItem("printerCardSize", "M");
    renderPage();
    await screen.findByText("Printer 1");
    expect(document.querySelector('[data-slot="printer-card"]')).toHaveAttribute("data-size", "M");
  });

  it("card size selector persists to localStorage and changes the grid", async () => {
    overviewBox.current = { printers: [printerItem(1)], up_next: [], recent_runs: [] };
    renderPage();
    await screen.findByText("Printer 1");
    fireEvent.click(screen.getByRole("radio", { name: "XL" }));
    expect(localStorage.getItem("printerCardSize")).toBe("XL");
    expect(document.querySelector('[data-slot="printer-card"]')).toHaveAttribute("data-size", "XL");
    fireEvent.click(screen.getByRole("radio", { name: "S" }));
    expect(localStorage.getItem("printerCardSize")).toBe("S");
    const grid = document.querySelector('[data-slot="printer-card"]')!.parentElement!.parentElement as HTMLElement;
    expect(grid.style.gridTemplateColumns).toContain("minmax(min(200px");
  });

  it("renders a CameraTile only for can_print printers on L/XL cards", async () => {
    localStorage.setItem("printerCardSize", "L");
    overviewBox.current = {
      printers: [printerItem(1, { can_print: true, can_control: false }), printerItem(2, { can_print: false, can_control: true })],
      up_next: [],
      recent_runs: [],
    };

    renderPage();

    await screen.findByText("Printer 1");
    expect(document.querySelectorAll('[data-slot="camera-tile"]')).toHaveLength(1);
  });

  it("renders no CameraTile on M cards", async () => {
    localStorage.setItem("printerCardSize", "M");
    overviewBox.current = { printers: [printerItem(1)], up_next: [], recent_runs: [] };
    renderPage();
    await screen.findByText("Printer 1");
    expect(document.querySelectorAll('[data-slot="camera-tile"]')).toHaveLength(0);
  });

  it("Q12: every M card carries the shared extras (camera split, firmware, history); S shows none", async () => {
    overviewBox.current = { printers: [printerItem(1), printerItem(2)], up_next: [], recent_runs: [] };
    renderPage();
    await screen.findByText("Printer 1");
    expect(await screen.findAllByRole("button", { name: "More" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "Camera options" })).toHaveLength(2);
  });

  it("Q12: S cards render no footer items", async () => {
    localStorage.setItem("printerCardSize", "S");
    overviewBox.current = { printers: [printerItem(1)], up_next: [], recent_runs: [] };
    renderPage();
    await screen.findByText("Printer 1");
    expect(screen.queryByRole("button", { name: "Firmware" })).toBeNull();
  });
});

function fleet(): PrintersOverviewOut {
  return {
    printers: [
      printerItem(1, { printer: { ...printerItem(1).printer, name: "Alpha", model: "A1 mini" }, status: status({ gcode_state: "RUNNING", mc_percent: 10, mc_remaining_time: 120 }) }),
      printerItem(2, { printer: { ...printerItem(2).printer, name: "Bravo", model: "X1 Carbon" }, status: status({ gcode_state: "PAUSE", mc_remaining_time: 30 }) }),
      printerItem(3, { printer: { ...printerItem(3).printer, name: "Charlie", model: "A1 mini" }, status: status({ gcode_state: "IDLE" }) }),
      printerItem(4, { printer: { ...printerItem(4).printer, name: "Delta", model: "P1S" }, status: status({ gcode_state: "FAILED" }) }),
      printerItem(5, { printer: { ...printerItem(5).printer, name: "Echo", model: "P1S" }, status: status({ online: false }) }),
    ],
    up_next: [],
    recent_runs: [],
  };
}

const cardNames = () =>
  [...document.querySelectorAll('[data-slot="card-header"] a')].map((a) => a.textContent);

describe("PrintDashboardPage summary, filters and sort", () => {
  it("summary bar counts each status and shows the next printer to free up", async () => {
    overviewBox.current = fleet();
    renderPage();
    await screen.findByText("Alpha");
    for (const [st, n] of [["printing", 1], ["paused", 1], ["available", 1], ["problem", 1], ["offline", 1], ["finished", 0]] as const) {
      expect(screen.getByRole("button", { name: `${st} ${n}` })).toBeInTheDocument();
    }
    expect(screen.getByTestId("next-free")).toHaveTextContent("Next free: Bravo in 30m");
  });

  it("clicking a status chip filters via ?status= and clicking again clears it", async () => {
    overviewBox.current = fleet();
    renderPage();
    await screen.findByText("Alpha");
    fireEvent.click(screen.getByRole("button", { name: "problem 1" }));
    await waitFor(() => expect(cardNames()).toEqual(["Delta"]));
    fireEvent.click(screen.getByRole("button", { name: "problem 1" }));
    await waitFor(() => expect(cardNames()).toHaveLength(5));
  });

  it("reads filters from the URL: ?status=offline", async () => {
    overviewBox.current = fleet();
    renderPage("/?status=offline");
    await waitFor(() => expect(cardNames()).toEqual(["Echo"]));
  });

  it("hide offline and search narrow the list", async () => {
    overviewBox.current = fleet();
    renderPage();
    await screen.findByText("Alpha");
    fireEvent.click(screen.getByLabelText("Hide offline"));
    await waitFor(() => expect(cardNames()).not.toContain("Echo"));
    fireEvent.change(screen.getByLabelText("Search printers"), { target: { value: "p1s" } });
    await waitFor(() => expect(cardNames()).toEqual(["Delta"]));
  });

  it("shows an empty-filter message", async () => {
    overviewBox.current = fleet();
    renderPage("/?q=zzz");
    expect(await screen.findByText("No printers match these filters.")).toBeInTheDocument();
  });

  it("a paused printer with a print_error counts as paused and is bulk-eligible", async () => {
    const o = fleet();
    o.printers[1] = printerItem(2, {
      printer: { ...printerItem(2).printer, name: "Bravo" },
      status: status({ gcode_state: "PAUSE", print_error: 5 }),
    });
    overviewBox.current = o;
    renderPage();
    await screen.findByText("Alpha");
    expect(screen.getByRole("button", { name: "paused 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "problem 1" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Select" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Bravo" }));
    expect(await screen.findByRole("button", { name: "Stop selected (1)" })).toBeEnabled();
  });

  it("sort by ETA puts the soonest-free printer first", async () => {
    overviewBox.current = fleet();
    renderPage("/?sort=eta");
    await waitFor(() => expect(cardNames().slice(0, 2)).toEqual(["Bravo", "Alpha"]));
  });

  it("sort by status groups under headers in status order", async () => {
    overviewBox.current = fleet();
    renderPage("/?sort=status");
    await screen.findByText("Alpha");
    const headers = [...document.querySelectorAll("h2")].map((h) => h.textContent?.replace(/\(\d+\)/, "").trim());
    expect(headers).toEqual(["Printing", "Paused", "Problem", "Available", "Offline"]);
  });

  it("sort by model groups by model name", async () => {
    overviewBox.current = fleet();
    renderPage("/?sort=model");
    await screen.findByText("Alpha");
    const headers = [...document.querySelectorAll("h2")].map((h) => h.textContent?.replace(/\(\d+\)/, "").trim());
    expect(headers).toEqual(["A1 mini", "P1S", "X1 Carbon"]);
  });
});

describe("PrintDashboardPage bulk select", () => {
  it("pauses / stops the selected printers with one request each, after confirmation", async () => {
    overviewBox.current = fleet();
    postMock.mockResolvedValue(undefined);
    renderPage();
    await screen.findByText("Alpha");
    fireEvent.click(screen.getByRole("button", { name: "Select" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Alpha" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Bravo" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Charlie" }));
    expect(await screen.findByText("3 selected")).toBeInTheDocument();
    // Only Alpha (printing) can pause; Alpha + Bravo can stop.
    fireEvent.click(screen.getByRole("button", { name: "Stop selected (2)" }));
    expect(postMock).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledTimes(2));
    expect(postMock).toHaveBeenCalledWith("/printers/1/stop", undefined);
    expect(postMock).toHaveBeenCalledWith("/printers/2/stop", undefined);
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
          has_snapshot: false,
          has_timelapse: false,
          media_note: null,
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
            wear_cost: null,
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

describe("PrintDashboardPage review inbox rail card (P7 Task 9)", () => {
  beforeEach(() => {
    overviewBox.current = { printers: [printerItem(1)], up_next: [], recent_runs: [] };
    reviewCountBox.current = { count: 0 };
    reviewInboxBox.current = [];
  });

  it("mounts the review inbox card when the viewer can review and the count is > 0", async () => {
    reviewCountBox.current = { count: 1 };
    reviewInboxBox.current = [
      {
        id: 9,
        model_id: 1,
        position: 1,
        added_at: "2026-10-22T10:00:00Z",
        model: { id: 1, slug: "m", name: "Reviewable Model" },
        printable_file: null,
        review_state: "pending",
        plate: null,
        printer_id: null,
      },
    ];
    renderPage();

    expect(await screen.findByText("Review inbox")).toBeInTheDocument();
    expect(await screen.findByText("Reviewable Model")).toBeInTheDocument();
  });

  it("omits the card when the count is 0", async () => {
    reviewCountBox.current = { count: 0 };
    renderPage();

    await screen.findByText("Print");
    expect(screen.queryByText("Review inbox")).not.toBeInTheDocument();
  });

  it("omits the card for a viewer who can't review", async () => {
    meBox.current = { ...meBox.current, role: "member", permissions: [] };
    reviewCountBox.current = { count: 2 };
    renderPage();

    await screen.findByText("Print");
    expect(screen.queryByText("Review inbox")).not.toBeInTheDocument();
  });
});

describe("PrintDashboardPage dock", () => {
  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: true, media: query, addEventListener: () => {}, removeEventListener: () => {},
    }));
    overviewBox.current = { printers: [printerItem(1)], up_next: [], recent_runs: [] };
  });

  it("keeps the classic page by default; Customize layout switches to the dock and remembers it", async () => {
    renderPage();
    await screen.findByText("Printer 1");
    expect(screen.queryByTestId("print-dock")).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "Customize layout" }));
    expect(await screen.findByTestId("print-dock")).toBeInTheDocument();
    expect(localStorage.getItem("tdmm.printDashDock.v1")).toBe("1");
    fireEvent.click(await screen.findByRole("button", { name: "Classic layout" }));
    await waitFor(() => expect(screen.queryByTestId("print-dock")).toBeNull());
    expect(localStorage.getItem("tdmm.printDashDock.v1")).toBeNull();
    expect(await screen.findByText("Printer 1")).toBeInTheDocument();
  });

  it("opens straight into the dock when the preference is stored", async () => {
    localStorage.setItem("tdmm.printDashDock.v1", "1");
    renderPage();
    expect(await screen.findByTestId("print-dock")).toBeInTheDocument();
  });
});
