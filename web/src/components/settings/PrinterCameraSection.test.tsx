import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PrinterOut } from "@/api/types";
import { PrinterCameraSection } from "@/components/settings/PrinterCameraSection";

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
      <PrinterCameraSection printer={PRINTER} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  [getMock, postMock, patchMock, deleteMock].forEach((m) => m.mockReset());
  getMock.mockResolvedValue([
    { id: 5, printer_id: 1, label: null, created_at: "2026-10-23T00:00:00Z", revoked_at: null },
  ]);
  patchMock.mockResolvedValue(PRINTER);
  postMock.mockResolvedValue({
    id: 6,
    printer_id: 1,
    label: null,
    created_at: "2026-10-23T00:00:00Z",
    revoked_at: null,
    token: "ov_abc",
    overlay_url: "/overlay/1?token=ov_abc",
  });
  deleteMock.mockResolvedValue(undefined);
});

describe("PrinterCameraSection", () => {
  it("saves external camera + rotation into options", async () => {
    renderSection();
    fireEvent.change(screen.getByLabelText("External camera"), { target: { value: "mjpeg" } });
    fireEvent.change(screen.getByLabelText("URL"), { target: { value: "http://cam/stream" } });
    fireEvent.change(screen.getByLabelText("Rotation"), { target: { value: "180" } });
    fireEvent.click(screen.getByLabelText(/Layer timelapse/));
    fireEvent.click(screen.getByRole("button", { name: "Save camera" }));

    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledWith("/printers/1", {
        options: { foo: 1, camera_rotation: 180, layer_timelapse: true, camera: { kind: "mjpeg", url: "http://cam/stream" } },
      }),
    );
  });

  it("mints an overlay token (URL shown once) and revokes one", async () => {
    renderSection();
    fireEvent.click(await screen.findByRole("button", { name: "Create overlay token" }));
    expect(await screen.findByTestId("minted-overlay-url")).toHaveTextContent("/overlay/1?token=ov_abc");
    expect(postMock).toHaveBeenCalledWith("/printers/1/overlay-tokens", {});

    fireEvent.click(await screen.findByRole("button", { name: "Revoke token 5" }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/printers/1/overlay-tokens/5"));
  });
});
