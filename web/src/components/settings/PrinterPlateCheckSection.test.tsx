import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PrinterOut } from "@/api/types";
import { PrinterPlateCheckSection } from "@/components/settings/PrinterPlateCheckSection";
import { QueueStateChip } from "@/pages/QueuePage";

const { getMock, postMock, patchMock, deleteMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  patchMock: vi.fn(),
  deleteMock: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock, patch: patchMock, delete: deleteMock } };
});

const PRINTER: PrinterOut = {
  id: 1,
  name: "A1",
  kind: "bambu_lan",
  host: "h",
  serial: "s",
  model: null,
  enabled: true,
  options: { foo: 1 },
  access_code_set: true,
  build_volume_mm: null,
};

function renderSection() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PrinterPlateCheckSection printer={PRINTER} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  [getMock, postMock, patchMock, deleteMock].forEach((m) => m.mockReset());
  getMock.mockResolvedValue([]);
  patchMock.mockResolvedValue(PRINTER);
});

describe("PrinterPlateCheckSection", () => {
  it("saves plate_check into options, preserving other options", async () => {
    renderSection();
    fireEvent.click(screen.getByLabelText("Check plate before print"));
    fireEvent.change(screen.getByLabelText("Threshold (% difference)"), { target: { value: "2.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save plate check" }));
    await waitFor(() => expect(patchMock).toHaveBeenCalled());
    expect(patchMock.mock.calls[0][1]).toEqual({
      options: { foo: 1, plate_check: { enabled: true, threshold: 2.5, plate_type: "" } },
    });
  });

  it("Test shows the verdict and preview", async () => {
    postMock.mockResolvedValue({
      empty: false,
      score: 4.2,
      threshold: 1,
      needs_calibration: false,
      message: "Objects detected on plate (4.20% difference)",
      references: 2,
      preview: "data:image/jpeg;base64,AAAA",
    });
    renderSection();
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByText(/Not empty/)).toBeTruthy();
    expect(screen.getByAltText("Annotated plate check")).toBeTruthy();
    expect(postMock.mock.calls[0][0]).toBe("/printers/1/check-plate");
  });

  it("wizard captures a reference", async () => {
    postMock.mockResolvedValue({ id: 9, plate_type: "", label: "", created_at: "2026-10-25T00:00:00Z" });
    renderSection();
    fireEvent.click(screen.getByRole("button", { name: "Calibrate" }));
    fireEvent.click(await screen.findByRole("button", { name: "The plate is empty" }));
    fireEvent.click(screen.getByRole("button", { name: "Capture reference" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/plate-references", { plate_type: "" }));
  });
});

describe("QueueStateChip plate hold", () => {
  it("renders a plate-not-empty chip for a held waiting entry", () => {
    render(<QueueStateChip state="waiting" lastError="plate not empty" />);
    expect(screen.getByText("Plate not empty")).toBeTruthy();
  });
});
