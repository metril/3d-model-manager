import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FileOut, Features, PrinterOut } from "@/api/types";
import { SendToPrinterButton } from "@/components/model-detail/SendToPrinterButton";

vi.mock("@/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/auth")>()),
  useAuth: () => ({ data: { id: 1, username: "tester" }, can: () => true, canPrinter: () => true }),
}));


// `vi.mock` factories are hoisted above the module's own top-level bindings
// (same pattern as SettingsPage.test.tsx/ScanReport.test.tsx), so the fakes
// have to be created through `vi.hoisted`.
const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock, post: postMock },
  };
});

const PRINTER: PrinterOut = {
  id: 1,
  name: "Bambu A1",
  kind: "bambu_lan",
  host: "192.168.1.50",
  serial: "AC12345",
  model: "A1 mini",
  enabled: true,
  options: {},
  access_code_set: true,
  build_volume_mm: null,
};

const SLICED_FILE: FileOut = {
  id: 7,
  revision_id: 1,
  rel_path: "print.gcode.3mf",
  storage_path: "/data/print.gcode.3mf",
  blob_hash: "slicedhash",
  size: 4096,
  format: "gcode_3mf",
  kind: "sliced",
  mtime: "2026-06-01T12:00:00Z",
  verified_at: "2026-06-01T12:00:05Z",
  meta: null,
  thumb_ready: false,
  glb_status: null,
  glb_preview_ready: false,
};

const MESH_FILE: FileOut = { ...SLICED_FILE, id: 8, format: "stl", kind: "mesh", rel_path: "model.stl" };

// Only `printer_enabled` matters to this component -- `Pick` keeps every
// call site above from having to also stub the Round 8 T6 slicer-watch
// fields.
function mockApi(features: Pick<Features, "printer_enabled">, printers: PrinterOut[]) {
  getMock.mockImplementation((path: string) => {
    if (path === "/features") return Promise.resolve(features);
    if (path === "/printers") return Promise.resolve(printers);
    return Promise.resolve([]);
  });
}

function renderButton(file: FileOut) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <SendToPrinterButton file={file} />
    </QueryClientProvider>,
  );
}

describe("SendToPrinterButton", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
  });

  it("renders nothing when the printer feature is disabled", async () => {
    mockApi({ printer_enabled: false }, [PRINTER]);

    const { container } = renderButton(SLICED_FILE);

    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/features"));
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for a non-sliced file even when the feature is enabled", async () => {
    mockApi({ printer_enabled: true }, [PRINTER]);

    const { container } = renderButton(MESH_FILE);

    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/features"));
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when there are no printers configured", async () => {
    mockApi({ printer_enabled: true }, []);

    const { container } = renderButton(SLICED_FILE);

    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/printers"));
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the Print trigger when enabled, sliced, and a printer exists", async () => {
    mockApi({ printer_enabled: true }, [PRINTER]);

    renderButton(SLICED_FILE);

    expect(await screen.findByRole("button", { name: `Print ${SLICED_FILE.rel_path}` })).toBeInTheDocument();
  });

  it("submits a print request for the default printer and plate 1", async () => {
    mockApi({ printer_enabled: true }, [PRINTER]);
    postMock.mockResolvedValue({ id: 1, printer_id: 1, file_id: SLICED_FILE.id, state: "queued" });

    renderButton(SLICED_FILE);

    fireEvent.click(await screen.findByRole("button", { name: `Print ${SLICED_FILE.rel_path}` }));
    fireEvent.click(await screen.findByRole("button", { name: "Send to printer" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/printers/1/print", {
        file_id: SLICED_FILE.id,
        plate: 1,
        use_ams: false,
        ams_mapping: [0],
        bed_levelling: true,
        flow_cali: true,
        timelapse: false,
      }),
    );
  });
});
