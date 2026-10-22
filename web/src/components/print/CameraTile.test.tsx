import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CameraTile } from "@/components/print/CameraTile";

const { postMock } = vi.hoisted(() => ({ postMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, post: postMock } };
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
});
