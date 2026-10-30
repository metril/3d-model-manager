import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CameraTile } from "@/components/print/CameraTile";

const { postMock, getMock } = vi.hoisted(() => ({ postMock: vi.fn(), getMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, post: postMock, get: getMock } };
});

// Controllable IntersectionObserver stub -- tests grab the live instance
// and fire its callback directly, same posture as a real browser reporting
// an intersection change.
class FakeIntersectionObserver {
  static instances: FakeIntersectionObserver[] = [];
  callback: IntersectionObserverCallback;
  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback;
    FakeIntersectionObserver.instances.push(this);
  }
  observe() {}
  disconnect() {}
  trigger(isIntersecting: boolean) {
    this.callback([{ isIntersecting } as IntersectionObserverEntry], this as unknown as IntersectionObserver);
  }
}

function renderTile(props: Partial<{ printerId: number; online: boolean }> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <CameraTile printerId={props.printerId ?? 1} online={props.online ?? true} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  postMock.mockReset();
  getMock.mockReset();
  getMock.mockRejectedValue(new Error("no status"));
  postMock.mockResolvedValue({
    stream_url: "/api/printers/1/camera/stream?token=abc",
    snapshot_url: "/api/printers/1/camera/snapshot?token=abc",
    expires_at: "2026-10-22T00:05:00Z",
  });
  FakeIntersectionObserver.instances = [];
  vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);
  Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("CameraTile", () => {
  it("mounts the MJPEG img only while visible", async () => {
    renderTile();

    const img = await screen.findByRole("img", { name: "Camera feed" });
    expect(img).toHaveAttribute("src", "/api/printers/1/camera/stream?token=abc");
    expect(postMock).toHaveBeenCalledTimes(1);

    const observer = FakeIntersectionObserver.instances[0]!;
    act(() => observer.trigger(false));
    expect(screen.queryByRole("img", { name: "Camera feed" })).not.toBeInTheDocument();

    act(() => observer.trigger(true));
    expect(await screen.findByRole("img", { name: "Camera feed" })).toBeInTheDocument();
    expect(postMock).toHaveBeenCalledTimes(1);
  });

  it("pauses when the document is hidden", async () => {
    renderTile();
    await screen.findByRole("img", { name: "Camera feed" });

    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(screen.queryByRole("img", { name: "Camera feed" })).not.toBeInTheDocument();

    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(await screen.findByRole("img", { name: "Camera feed" })).toBeInTheDocument();
  });

  it("shows offline state when printer offline and never requests a token", async () => {
    renderTile({ online: false });

    expect(await screen.findByText("Camera offline")).toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalled();
  });

  it("img error -> offline, then retries", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderTile();

    const img = await screen.findByRole("img", { name: "Camera feed" });
    fireEvent.error(img);

    expect(screen.getByText("Camera offline")).toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });

    expect(postMock).toHaveBeenCalledTimes(2);
    expect(await screen.findByRole("img", { name: "Camera feed" })).toBeInTheDocument();
  });

  it("applies rotation + fps from camera status and shows the beta badge", async () => {
    getMock.mockImplementation((path: string) =>
      path.endsWith("/camera/status")
        ? Promise.resolve({ status: "idle", source: "rtsp", beta: true, rotation: 90, default_fps: 5 })
        : Promise.reject(new Error("x")),
    );
    renderTile();

    expect(await screen.findByText("beta")).toBeInTheDocument();
    const img = await screen.findByRole("img", { name: "Camera feed" });
    expect(img.style.transform).toContain("rotate(90deg)");
    expect(img).toHaveAttribute("src", "/api/printers/1/camera/stream?token=abc&fps=5");
  });

  it("reconnects with backoff after a 20s no-frame stall", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderTile();
    await screen.findByRole("img", { name: "Camera feed" });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(21_500);
    });
    // first backoff is 5s -> img remounted with a reconnect nonce
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_500);
    });
    expect(screen.getByRole("img", { name: "Camera feed" })).toHaveAttribute(
      "src",
      "/api/printers/1/camera/stream?token=abc&_r=1",
    );
  });

  it("gives up to the offline state after repeated stalls", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderTile();
    await screen.findByRole("img", { name: "Camera feed" });
    // 5 reconnects (20s stall + 5/10/20/40/60s backoff each) then the 6th stall.
    for (let t = 0; t < 400 && !screen.queryByText("Camera offline"); t += 2) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2_000);
      });
    }
    expect(screen.getByText("Camera offline")).toBeInTheDocument();
  });

  it("drops the img src on unmount", async () => {
    const { unmount } = renderTile();
    const img = await screen.findByRole("img", { name: "Camera feed" });
    unmount();
    expect(img.getAttribute("src")).toBe("");
  });

  it("hides the light toggle without control permission and uses token urls without calls", async () => {
    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <CameraTile
          printerId={7}
          online
          urls={{ stream_url: "/s?token=t", snapshot_url: "/p?token=t" }}
          rotation={180}
        />
      </QueryClientProvider>,
    );
    const img = await screen.findByRole("img", { name: "Camera feed" });
    expect(img).toHaveAttribute("src", "/s?token=t");
    expect(img.style.transform).toContain("rotate(180deg)");
    expect(screen.queryByRole("button", { name: /light/i })).not.toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalled();
    expect(getMock).not.toHaveBeenCalled();
  });

  it("does not CSS-rotate snapshot mode (backend rotates stills)", async () => {
    getMock.mockImplementation((path: string) =>
      path.endsWith("/camera/status")
        ? Promise.resolve({ status: "idle", source: "a1", beta: false, rotation: 90, default_fps: 5 })
        : Promise.reject(new Error("x")),
    );
    renderTile();
    const img = await screen.findByRole("img", { name: "Camera feed" });
    await vi.waitFor(() => expect(img.style.transform).toContain("rotate(90deg)"));
    fireEvent.click(screen.getByRole("button", { name: "Switch to snapshots" }));
    const snap = await screen.findByRole("img", { name: "Camera feed" });
    expect(snap.style.transform).toBe("");
  });

  async function runWithFrames(frameFor: () => number[], ms: number) {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const orig = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = (() => ({
      drawImage: () => {},
      getImageData: () => ({ data: new Uint8ClampedArray(frameFor()) }),
    })) as unknown as typeof orig;
    const nw = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, "naturalWidth");
    Object.defineProperty(HTMLImageElement.prototype, "naturalWidth", { value: 10, configurable: true });
    try {
      renderTile();
      await screen.findByRole("img", { name: "Camera feed" });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(ms);
      });
      return screen.getByRole("img", { name: "Camera feed" }).getAttribute("src");
    } finally {
      HTMLCanvasElement.prototype.getContext = orig;
      if (nw) Object.defineProperty(HTMLImageElement.prototype, "naturalWidth", nw);
    }
  }

  it("does not reconnect a live scene whose frames differ by sensor noise", async () => {
    let n = 0;
    const src = await runWithFrames(() => [n++ % 7, 1, 2, 3], 60_000);
    expect(src).toBe("/api/printers/1/camera/stream?token=abc");
  });

  it("reconnects when frames are byte-identical for 20s", async () => {
    const src = await runWithFrames(() => [1, 2, 3, 4], 30_000);
    expect(src).toContain("_r=1");
  });

  it("errored retry fires even when urls is a fresh object each render", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const qc = new QueryClient();
    const ui = () => (
      <QueryClientProvider client={qc}>
        <CameraTile printerId={7} online urls={{ stream_url: "/s?token=t", snapshot_url: "/p?token=t" }} />
      </QueryClientProvider>
    );
    const { rerender } = render(ui());
    fireEvent.error(await screen.findByRole("img", { name: "Camera feed" }));
    expect(screen.getByText("Camera offline")).toBeInTheDocument();
    for (let i = 0; i < 9; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_000);
      });
      rerender(ui());
    }
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_500);
    });
    expect(screen.getByRole("img", { name: "Camera feed" })).toBeInTheDocument();
  });
});
