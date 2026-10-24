import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PrinterOut } from "@/api/types";
import { PrinterDetectionSection } from "@/components/settings/PrinterDetectionSection";

const { getMock, putMock } = vi.hoisted(() => ({ getMock: vi.fn(), putMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, put: putMock } };
});

const PRINTER = {
  id: 3,
  name: "A1",
  kind: "bambu_lan",
  host: "h",
  serial: "s",
  model: null,
  enabled: true,
  options: {},
  access_code_set: true,
  build_volume_mm: null,
} as PrinterOut;

const DETECTION = {
  enabled: false,
  action: null,
  effective_action: "notify",
  global_enabled: false,
  status: { active: false, state: null, score: null, smoothed: null, detection_paused: false },
};

beforeEach(() => {
  getMock.mockReset().mockResolvedValue(DETECTION);
  putMock.mockReset().mockImplementation((_u: string, b: object) => Promise.resolve({ ...DETECTION, ...b }));
});

function renderSection() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PrinterDetectionSection printer={PRINTER} />
    </QueryClientProvider>,
  );
}

describe("PrinterDetectionSection", () => {
  it("toggles detection and keeps the action", async () => {
    renderSection();
    fireEvent.click(await screen.findByLabelText("Watch this printer"));
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("/printers/3/detection", { enabled: true, action: null }),
    );
  });

  it("sets an action override and warns when globally off", async () => {
    renderSection();
    fireEvent.change(await screen.findByLabelText("Action"), { target: { value: "pause" } });
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("/printers/3/detection", { enabled: false, action: "pause" }),
    );
    expect(screen.getByText(/off globally/)).toBeInTheDocument();
  });
});
