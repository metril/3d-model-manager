import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { bedFromProfile } from "@/api/combine";
import type { FileOut } from "@/api/types";
import { CombineDialog } from "@/components/model-detail/CombineDialog";

const { getMock, postMock, toastMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  toastMock: { error: vi.fn(), success: vi.fn() },
}));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock } };
});
vi.mock("sonner", () => ({ toast: toastMock }));

const FILES = [
  { id: 1, rel_path: "parts/a.stl", format: "stl" },
  { id: 2, rel_path: "b.stl", format: "stl" },
] as unknown as FileOut[];

const PROFILE = {
  id: 7,
  name: "A1 mini",
  kind: "printer",
  data: { printable_area: ["0x0", "180x0", "180x180", "0x180"] },
};

function plan(fits: boolean) {
  return {
    fits,
    bed: { x_mm: 180, y_mm: 180 },
    required_bed: { x_mm: fits ? 50 : 400, y_mm: 50 },
    placements: [
      { file_id: 1, x_mm: 0, y_mm: 0, w_mm: 20, d_mm: 10 },
      { file_id: 2, x_mm: 30, y_mm: 0, w_mm: 20, d_mm: 10 },
    ],
    file: null,
  };
}

function renderDialog(onOpenChange = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <CombineDialog open onOpenChange={onOpenChange} slug="widget" files={FILES} />
    </QueryClientProvider>,
  );
  return onOpenChange;
}

beforeEach(() => {
  vi.clearAllMocks();
  getMock.mockResolvedValue({ items: [PROFILE], total: 1, nozzle_source: null });
});

describe("bedFromProfile", () => {
  it("reads the printable area", () => {
    expect(bedFromProfile(PROFILE.data)).toEqual({ x_mm: 180, y_mm: 180 });
    expect(bedFromProfile({})).toBeNull();
  });
});

describe("CombineDialog", () => {
  it("previews the fit and submits with per-file copies", async () => {
    postMock.mockImplementation((_path: string, body: { dry_run: boolean }) =>
      Promise.resolve(body.dry_run ? plan(true) : { ...plan(true), file: { rel_path: "combined.3mf" } }),
    );
    const onOpenChange = renderDialog();

    await waitFor(() => expect(screen.getAllByTestId("combine-rect")).toHaveLength(2));
    expect(screen.getByText(/Fits: 2 objects/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Copies of a.stl"), { target: { value: "3" } });
    await waitFor(() =>
      expect(postMock).toHaveBeenLastCalledWith(
        "/models/widget/combine",
        expect.objectContaining({
          dry_run: true,
          items: [
            { file_id: 1, copies: 3 },
            { file_id: 2, copies: 1 },
          ],
        }),
      ),
    );

    fireEvent.click(await screen.findByRole("button", { name: "Combine" }));
    await waitFor(() => expect(toastMock.success).toHaveBeenCalled());
    expect(postMock).toHaveBeenCalledWith(
      "/models/widget/combine",
      expect.objectContaining({ dry_run: false, items: [{ file_id: 1, copies: 3 }, { file_id: 2, copies: 1 }] }),
    );
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("blocks submit and shows the required bed when it does not fit", async () => {
    postMock.mockResolvedValue(plan(false));
    renderDialog();

    expect(await screen.findByText(/Does not fit/)).toBeTruthy();
    expect(screen.getByText(/needs about 400/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Combine" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("uses the chosen printer profile instead of a custom bed", async () => {
    postMock.mockResolvedValue(plan(true));
    renderDialog();
    await screen.findByRole("option", { name: /A1 mini/ });

    fireEvent.change(screen.getByLabelText("Printer / bed"), { target: { value: "7" } });
    await waitFor(() =>
      expect(postMock).toHaveBeenLastCalledWith(
        "/models/widget/combine",
        expect.objectContaining({ printer_profile_id: 7, bed: null }),
      ),
    );
  });
});
