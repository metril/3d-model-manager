import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FloatingCamera } from "@/components/camera/FloatingCamera";
import { CameraTile } from "@/components/print/CameraTile";
import { floatingCamera } from "@/lib/floatingCamera";

const { postMock, getMock } = vi.hoisted(() => ({ postMock: vi.fn(), getMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, post: postMock, get: getMock } };
});

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

beforeEach(() => {
  localStorage.clear();
  floatingCamera.reload();
  getMock.mockReset();
  getMock.mockImplementation((path: string) =>
    path === "/printers/overview"
      ? Promise.resolve({ printers: [{ printer: { id: 1, name: "A1" }, status: { online: true } }] })
      : Promise.reject(new Error("x")),
  );
  postMock.mockReset();
  postMock.mockResolvedValue({
    stream_url: "/s?token=abc",
    snapshot_url: "/snap?token=abc",
    expires_at: "2026-10-22T00:05:00Z",
  });
});
afterEach(() => floatingCamera.close());

describe("floatingCamera store", () => {
  it("persists printer and rect to localStorage", () => {
    floatingCamera.open(3);
    floatingCamera.setRect({ x: 50, y: 60, w: 300, h: 200 });
    floatingCamera.reload();
    expect(floatingCamera.get().printerId).toBe(3);
    expect(floatingCamera.get().rect).toMatchObject({ x: 50, y: 60, w: 300, h: 200 });
  });
  it("toggle closes when already showing that printer", () => {
    floatingCamera.toggle(2);
    expect(floatingCamera.get().printerId).toBe(2);
    floatingCamera.toggle(2);
    expect(floatingCamera.get().printerId).toBeNull();
  });
});

describe("FloatingCamera", () => {
  it("shares one stream: the inline tile yields while the overlay is open", async () => {
    render(
      wrap(
        <>
          <CameraTile printerId={1} online />
          <FloatingCamera />
        </>,
      ),
    );
    await screen.findAllByAltText("Camera feed");
    expect(screen.getAllByAltText("Camera feed")).toHaveLength(1);
    await act(async () => floatingCamera.open(1));
    const imgs = await screen.findAllByAltText("Camera feed");
    expect(imgs).toHaveLength(1);
    expect(screen.getByText("Showing in floating camera")).toBeTruthy();
    expect(screen.getByTestId("floating-camera").contains(imgs[0]!)).toBe(true);
    await act(async () => floatingCamera.setMinimized(true));
    expect(screen.queryByText("Showing in floating camera")).toBeNull();
  });

  it("closes on Escape and via the close button", async () => {
    render(wrap(<FloatingCamera />));
    expect(screen.queryByTestId("floating-camera")).toBeNull();
    await act(async () => floatingCamera.open(1));
    expect(screen.getByTestId("floating-camera")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("floating-camera")).toBeNull();
    await act(async () => floatingCamera.open(1));
    fireEvent.click(screen.getByLabelText("Close camera"));
    expect(screen.queryByTestId("floating-camera")).toBeNull();
  });

  it("persists across remount (route change) and minimises", async () => {
    const { unmount } = render(wrap(<FloatingCamera />));
    await act(async () => floatingCamera.open(1));
    unmount();
    render(wrap(<FloatingCamera />));
    expect(screen.getByTestId("floating-camera")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Minimise camera"));
    expect(screen.queryByAltText("Camera feed")).toBeNull();
    expect(screen.getByLabelText("Restore camera")).toBeTruthy();
  });
});
