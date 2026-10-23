import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { selectLive } from "@/lib/camWall";
import { CamWallPage } from "@/pages/CamWallPage";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock } };
});

describe("selectLive", () => {
  it("caps live tiles at max in list order", () => {
    expect([...selectLive([1, 2, 3, 4], 2)]).toEqual([1, 2]);
    expect(selectLive([1, 2], 0).size).toBe(0);
  });
  it("puts promoted ids first and ignores unknown ones", () => {
    expect([...selectLive([1, 2, 3], 1, [3])]).toEqual([3]);
    expect([...selectLive([1, 2, 3], 2, [9, 2])]).toEqual([2, 1]);
  });
});

const item = (id: number) => ({
  printer: { id, name: `P${id}`, enabled: true },
  status: { online: true, gcode_state: "IDLE" },
  can_print: true,
  can_control: false,
});

describe("CamWallPage", () => {
  beforeEach(() => {
    localStorage.clear();
    getMock.mockReset();
    getMock.mockImplementation((path: string) =>
      path === "/printers/overview"
        ? Promise.resolve({ printers: [item(1), item(2), item(3)] })
        : Promise.reject(new Error("x")),
    );
    postMock.mockReset();
    postMock.mockResolvedValue({ stream_url: "/s?token=a", snapshot_url: "/p?token=a", expires_at: "" });
  });

  function renderWall() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={qc}>
        <CamWallPage />
      </QueryClientProvider>,
    );
  }

  it("streams only maxLive tiles (persisted) and promotes a clicked still", async () => {
    localStorage.setItem("camWallMaxLive", "1");
    renderWall();
    const t1 = await screen.findByTestId("wall-tile-1");
    expect(t1).toHaveAttribute("data-live", "true");
    expect(screen.getByTestId("wall-tile-2")).toHaveAttribute("data-live", "false");

    fireEvent.click(screen.getByTestId("wall-tile-3"));
    expect(screen.getByTestId("wall-tile-3")).toHaveAttribute("data-live", "true");
    expect(screen.getByTestId("wall-tile-1")).toHaveAttribute("data-live", "false");

    fireEvent.change(screen.getByLabelText("Max live"), { target: { value: "3" } });
    expect(localStorage.getItem("camWallMaxLive")).toBe("3");
    expect(screen.getByTestId("wall-tile-2")).toHaveAttribute("data-live", "true");
  });
});
