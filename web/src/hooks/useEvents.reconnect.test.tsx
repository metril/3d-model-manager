import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EventsProvider, RECONNECT_BASE_MS } from "@/hooks/useEvents";

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: vi.fn().mockResolvedValue(undefined) } };
});

class FakeES {
  static instances: FakeES[] = [];
  readyState = 1;
  closed = false;
  onmessage: unknown = null;
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;
  url: string;
  constructor(url: string) {
    this.url = url;
    FakeES.instances.push(this);
  }
  close() {
    this.closed = true;
    this.readyState = 2;
  }
}

function mount() {
  const qc = new QueryClient();
  const spy = vi.spyOn(qc, "invalidateQueries");
  const view = render(
    <QueryClientProvider client={qc}>
      <EventsProvider>
        <div />
      </EventsProvider>
    </QueryClientProvider>,
  );
  return { spy, view };
}

describe("EventsProvider reconnect", () => {
  beforeEach(() => {
    FakeES.instances = [];
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", FakeES);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("recreates a CLOSED source with exponential backoff and resyncs on re-open", () => {
    const { spy } = mount();
    const first = FakeES.instances[0];
    first.onopen?.();
    first.readyState = 2;
    first.onerror?.();
    expect(FakeES.instances).toHaveLength(1);
    vi.advanceTimersByTime(RECONNECT_BASE_MS);
    expect(FakeES.instances).toHaveLength(2);

    const second = FakeES.instances[1];
    second.readyState = 2;
    second.onerror?.(); // backoff doubles
    vi.advanceTimersByTime(RECONNECT_BASE_MS);
    expect(FakeES.instances).toHaveLength(2);
    vi.advanceTimersByTime(RECONNECT_BASE_MS);
    expect(FakeES.instances).toHaveLength(3);

    spy.mockClear();
    FakeES.instances[2].onopen?.();
    const keys = spy.mock.calls.map((c) => (c[0] as { queryKey: string[] }).queryKey[0]);
    expect(keys).toEqual(expect.arrayContaining(["archives", "spools", "imports", "scan", "revisions", "slice-job"]));
  });

  it("does not recreate a still-connecting source, and stops on unmount", () => {
    const { view } = mount();
    const first = FakeES.instances[0];
    first.readyState = 0; // CONNECTING: the browser retries by itself
    first.onerror?.();
    vi.advanceTimersByTime(60_000);
    expect(FakeES.instances).toHaveLength(1);

    first.readyState = 2;
    first.onerror?.();
    view.unmount();
    vi.advanceTimersByTime(60_000);
    expect(FakeES.instances).toHaveLength(1);
    expect(first.closed).toBe(true);
  });
});
