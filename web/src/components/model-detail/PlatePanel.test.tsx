import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PlatePanel } from "@/components/model-detail/PlatePanel";
import type { BlobMetaOut, FileOut, PlateOut } from "@/api/types";

// `PlatePanel` reads `useAppSettings()` (R11-B item 14: per-plate cost
// estimate) -- give it a QueryClient + a well-shaped `/settings/app` so
// mounting it never throws or hangs on an unresolved fetch.
const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock } };
});

const DEFAULT_APP_SETTINGS = {
  printer_enabled: false,
  scan_interval_s: 0,
  collection_sync_interval_s: 0,
  watch_interval_s: 0,
  watch_stable_s: 10,
  filament_cost_per_kg: 20,
  machine_cost_per_hour: 0,
};

function renderPlatePanel(file: FileOut, appSettings = DEFAULT_APP_SETTINGS) {
  getMock.mockImplementation((path: string) =>
    path === "/settings/app" ? Promise.resolve(appSettings) : Promise.resolve({}),
  );
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <PlatePanel file={file} />
    </QueryClientProvider>,
  );
}

// `GcodePreview` is a `React.lazy` chunk wrapping `gcode-preview`'s own
// WebGL renderer -- stub it so the "Preview layers" button's lazy-mount
// wiring can be exercised without touching three.js/WebGL in jsdom.
const gcodePreviewMock = vi.fn(({ fileId }: { fileId: number }) => (
  <div data-testid="gcode-preview-stub" data-file-id={fileId} />
));
vi.mock("@/components/viewer/GcodePreview", () => ({ default: gcodePreviewMock }));

const PLATE_1: PlateOut = {
  index: 1,
  prediction_s: 5400,
  weight_g: 42.4,
  thumbnail_available: true,
  est_cost: null,
  filaments: [{ type: "PLA", color: "#ff0000", used_m: 12.3, used_g: 36.8 }],
};

const PLATE_2: PlateOut = {
  index: 2,
  prediction_s: 2700,
  weight_g: 18,
  thumbnail_available: false,
  est_cost: null,
  filaments: [
    { type: "PLA", color: "#ff0000", used_m: 5, used_g: 15 },
    { type: "PETG", color: null, used_m: 1, used_g: 3 },
  ],
};

function fakeFile(meta: Partial<BlobMetaOut> | null): FileOut {
  return {
    id: 1,
    revision_id: 1,
    rel_path: "print.gcode.3mf",
    storage_path: "/data/print.gcode.3mf",
    blob_hash: "platehash",
    size: 4096,
    format: "gcode_3mf",
    kind: "sliced",
    mtime: "2026-06-01T12:00:00Z",
    verified_at: "2026-06-01T12:00:05Z",
    thumb_ready: false,
    glb_status: null,
    glb_preview_ready: false,
    meta:
      meta === null
        ? null
        : {
            triangle_count: null,
            dims_mm: null,
            volume_cm3: null,
            surface_area_cm2: null,
            is_watertight: null,
            print_time_s: null,
            filament_g: null,
            filament_m: null,
            filament_types: null,
            layer_height: null,
            nozzle: null,
            printer_model: null,
            plate_count: null,
            layer_count: null,
            infill_pct: null,
            slicer: null,
            plates: null,
            ...meta,
          },
  };
}

describe("PlatePanel", () => {
  beforeEach(() => {
    getMock.mockReset();
  });

  it("shows the printer/nozzle/layer-height header line", () => {
    renderPlatePanel(
      fakeFile({ printer_model: "A1 mini", nozzle: 0.4, layer_height: 0.2, plates: [PLATE_1] }),
    );

    expect(screen.getByText("A1 mini · 0.4 mm nozzle · 0.2 mm layers")).toBeInTheDocument();
  });

  it("renders a plate image when the thumbnail is available, with its caption and filament chip", () => {
    renderPlatePanel(fakeFile({ plates: [PLATE_1] }));

    const image = screen.getByRole("img", { name: "Plate 1" });
    expect(image).toHaveAttribute("src", "/api/blobs/platehash/plates/1/thumb");
    expect(screen.getByText("Plate 1 · 1h 30m · 42 g")).toBeInTheDocument();
    expect(screen.getByText("PLA 37 g")).toBeInTheDocument();
  });

  it("falls back to an icon (no image) when a plate's thumbnail isn't available, and lists multiple filament chips", () => {
    renderPlatePanel(fakeFile({ plates: [PLATE_1, PLATE_2] }));

    expect(screen.queryByRole("img", { name: "Plate 2" })).not.toBeInTheDocument();
    expect(screen.getByText("Plate 2 · 45m · 18 g")).toBeInTheDocument();
    expect(screen.getByText("PLA 15 g")).toBeInTheDocument();
    expect(screen.getByText("PETG 3 g")).toBeInTheDocument();
  });

  it("renders both plate cards from the 2-plate fixture", () => {
    renderPlatePanel(fakeFile({ plates: [PLATE_1, PLATE_2] }));

    expect(screen.getByText(/^Plate 1 ·/)).toBeInTheDocument();
    expect(screen.getByText(/^Plate 2 ·/)).toBeInTheDocument();
  });

  it("gives a color swatch its filament color as an inline background", () => {
    const { container } = renderPlatePanel(fakeFile({ plates: [PLATE_1] }));

    const swatch = container.querySelector('span[style*="background-color"]');
    expect(swatch).not.toBeNull();
    expect((swatch as HTMLElement).style.backgroundColor).toBe("rgb(255, 0, 0)");
  });

  it("adds an estimated cost once app settings have loaded (R11-B item 14)", async () => {
    renderPlatePanel(fakeFile({ plates: [PLATE_1] }), {
      ...DEFAULT_APP_SETTINGS,
      filament_cost_per_kg: 20,
      machine_cost_per_hour: 0,
    });

    // 42.4g @ 20/kg = 0.848 -> "0.85"
    await waitFor(() => expect(screen.getByText("Plate 1 · 1h 30m · 42 g · Est. cost: 0.85")).toBeInTheDocument());
  });

  it("shows an empty state when the file has no plate data yet", () => {
    renderPlatePanel(fakeFile(null));

    expect(screen.getByText("No plate details available yet.")).toBeInTheDocument();
  });

  it("shows the slicer/duration/filament/infill metadata line (R10-B)", () => {
    renderPlatePanel(
      fakeFile({
        slicer: "OrcaSlicer",
        print_time_s: 3690,
        filament_g: 12.5,
        layer_height: 0.2,
        infill_pct: 15,
        filament_types: ["PLA"],
        plates: [PLATE_1],
      }),
    );

    expect(screen.getByText("OrcaSlicer · 1h 2m · 13 g · 0.2 mm layers · 15% infill · PLA")).toBeInTheDocument();
  });

  it("omits the metadata line entirely when every field is null", () => {
    renderPlatePanel(fakeFile({ plates: [PLATE_1] }));

    expect(screen.queryByText(/infill/)).not.toBeInTheDocument();
  });

  it("only lazily mounts the g-code preview after clicking 'Preview layers'", async () => {
    renderPlatePanel(fakeFile({ plates: [PLATE_1] }));

    expect(screen.queryByTestId("gcode-preview-stub")).not.toBeInTheDocument();
    expect(gcodePreviewMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /preview layers/i }));

    await waitFor(() => expect(screen.getByTestId("gcode-preview-stub")).toBeInTheDocument());
    expect(screen.getByTestId("gcode-preview-stub")).toHaveAttribute("data-file-id", "1");
  });
});
