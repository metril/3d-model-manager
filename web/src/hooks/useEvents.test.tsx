import { useEffect } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { JobUpdatedEvent } from "@/api/types";
import { EventsProvider, useEvents } from "@/hooks/useEvents";

// `vi.mock` factories are hoisted above the module's own top-level bindings
// (same pattern as LibraryPage.test.tsx), so the fake has to be created
// through `vi.hoisted`.
const { getMock } = vi.hoisted(() => ({ getMock: vi.fn().mockResolvedValue(undefined) }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock },
  };
});

// jsdom has no `EventSource` implementation -- this stub captures just
// enough of the interface (the `onerror` handler `EventsProvider` assigns)
// for the test to trigger it directly, without a real SSE connection.
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

/** Registers a `job.updated` listener via `useEvents().subscribe`, so a test
 * can assert the union branch for `print_job.updated` does NOT fan out to
 * `job.updated` listeners. */
function JobUpdatedListener({ onEvent }: { onEvent: (event: JobUpdatedEvent) => void }) {
  const { subscribe } = useEvents();
  useEffect(() => subscribe(onEvent), [subscribe, onEvent]);
  return null;
}

function renderProvider() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <EventsProvider>
        <div>ready</div>
      </EventsProvider>
    </QueryClientProvider>,
  );
}

describe("EventsProvider jobs invalidation", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("invalidates the jobs query on every job.updated transition, not just terminal ones", () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    render(
      <QueryClientProvider client={queryClient}>
        <EventsProvider>
          <div>ready</div>
        </EventsProvider>
      </QueryClientProvider>,
    );
    const source = FakeEventSource.instances[0];
    expect(source).toBeDefined();

    source.onmessage?.({
      data: JSON.stringify({
        type: "job.updated",
        job_id: "1",
        job_type: "convert_to_glb",
        state: "running",
        subject_type: "file",
        subject_id: 1,
      }),
    } as MessageEvent<string>);

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["jobs"] });
  });
});

describe("EventsProvider scan invalidation", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("invalidates the scan query when a scan_library job.updated event arrives", () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    render(
      <QueryClientProvider client={queryClient}>
        <EventsProvider>
          <div>ready</div>
        </EventsProvider>
      </QueryClientProvider>,
    );
    const source = FakeEventSource.instances[0];
    expect(source).toBeDefined();

    source.onmessage?.({
      data: JSON.stringify({
        type: "job.updated",
        job_id: "1",
        job_type: "scan_library",
        state: "running",
        subject_type: "scan_run",
        subject_id: 1,
      }),
    } as MessageEvent<string>);

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["scan"] });
  });

  it("does not invalidate the scan query for a non-scan job event", () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    render(
      <QueryClientProvider client={queryClient}>
        <EventsProvider>
          <div>ready</div>
        </EventsProvider>
      </QueryClientProvider>,
    );
    const source = FakeEventSource.instances[0];

    source.onmessage?.({
      data: JSON.stringify({
        type: "job.updated",
        job_id: "1",
        job_type: "convert_to_glb",
        state: "done",
        subject_type: "file",
        subject_id: 1,
      }),
    } as MessageEvent<string>);

    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ["scan"] });
  });
});

describe("EventsProvider print_job.updated", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // Fix-review finding 8: `["printers", "overview"]` was invalidated
  // explicitly alongside plain `["printers"]`, which already prefix-matches
  // it under react-query's default partial matching -- a redundant call.
  it("invalidates print-jobs and printers queries (no separate overview call), without notifying job.updated listeners", () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const jobUpdatedListener = vi.fn();

    render(
      <QueryClientProvider client={queryClient}>
        <EventsProvider>
          <JobUpdatedListener onEvent={jobUpdatedListener} />
        </EventsProvider>
      </QueryClientProvider>,
    );
    const source = FakeEventSource.instances[0];
    expect(source).toBeDefined();

    source.onmessage?.({
      data: JSON.stringify({ type: "print_job.updated", print_job_id: 1, printer_id: 1, state: "printing" }),
    } as MessageEvent<string>);

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["print-jobs"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["printers"] });
    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ["printers", "overview"] });
    expect(jobUpdatedListener).not.toHaveBeenCalled();
  });
});

describe("EventsProvider onerror session probe", () => {
  beforeEach(() => {
    getMock.mockClear();
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("probes /auth/me on a connection error, throttled to at most once per 5s", () => {
    renderProvider();
    const source = FakeEventSource.instances[0];
    expect(source).toBeDefined();

    // Repeated `onerror` firings within the throttle window (e.g. the
    // browser's own reconnect retries during a network blip) must not
    // each trigger a probe.
    source.onerror?.();
    source.onerror?.();
    source.onerror?.();
    expect(getMock).toHaveBeenCalledTimes(1);
    expect(getMock).toHaveBeenCalledWith("/auth/me");

    vi.advanceTimersByTime(5000);
    source.onerror?.();
    expect(getMock).toHaveBeenCalledTimes(2);
  });
});
