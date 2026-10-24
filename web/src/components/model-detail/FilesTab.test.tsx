import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { modelQueryOptions } from "@/api/library";
import { FilesTab } from "@/components/model-detail/FilesTab";
import type { FileOut, Features, ModelDetail, PrinterOut } from "@/api/types";

vi.mock("@/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/auth")>()),
  useAuth: () => ({ data: { id: 1, username: "tester" }, can: () => true, canPrinter: () => true }),
}));


// `vi.mock` factories are hoisted above the module's own top-level bindings
// (same pattern as SettingsPage.test.tsx), so the fake has to be created
// through `vi.hoisted`. The Files-tab `SendToPrinterButton` calls
// `useFeatures`/`usePrinters` (both `api.get`) -- the default mock below
// resolves `/features` to `undefined`, i.e. `printer_enabled` falsy, so the
// button self-hides in every test in this file except the ones that
// explicitly opt in.
const { getMock, uploadFileMock } = vi.hoisted(() => ({
  getMock: vi.fn().mockResolvedValue(undefined),
  uploadFileMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock },
  };
});

vi.mock("@/api/upload", () => ({ uploadFile: uploadFileMock }));

// The Add-files action mounts `UploadDropzone`, which subscribes to the
// app-wide SSE connection via `useEvents` -- unavailable in jsdom without an
// `EventsProvider` (same stub as UploadPage.test.tsx/UploadDropzone.test.tsx).
vi.mock("@/hooks/eventsContext", () => ({
  useEvents: () => ({ subscribe: () => () => {} }),
}));

const VERIFIED_FILE: FileOut = {
  id: 1,
  revision_id: 1,
  rel_path: "model.stl",
  storage_path: "/data/model.stl",
  blob_hash: "abc123def456",
  size: 2048,
  format: "stl",
  kind: "mesh",
  mtime: "2026-06-01T12:00:00Z",
  verified_at: "2026-06-01T12:00:05Z",
  meta: null,
  thumb_ready: false,
  glb_status: null,
  glb_preview_ready: false,
};

const PROCESSING_FILE: FileOut = {
  ...VERIFIED_FILE,
  id: 2,
  rel_path: "still-processing.stl",
  verified_at: null,
};

function buildModel(files: FileOut[]): ModelDetail {
  return {
    id: 1,
    slug: "test-model",
    name: "Test Model",
    description: null,
    source_url: null,
    source_site: null,
    source_author: null,
    source_license: null,
    source_collection_id: null,
    source_collection_title: null,
    imported_at: null,
    cover_blob_hash: null,
    is_archived: false,
    created_at: "2026-06-01T12:00:00Z",
    updated_at: "2026-06-01T12:00:00Z",
    tags: [],
    notes: [],
    backends: [],
    favorite: false,
    print_count: 0,
    last_printed_at: null,
    metadata: null,
    print_tips: null,
    current_revision: {
      id: 1,
      model_id: 1,
      number: 1,
      name: null,
      note: null,
      dir_name: "r1",
      created_at: "2026-06-01T12:00:00Z",
      files,
      notes: [],
    },
  };
}

function renderFilesTab(files: FileOut[], onViewIn3D?: (file: FileOut) => void) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <FilesTab model={buildModel(files)} onViewIn3D={onViewIn3D} />
    </QueryClientProvider>,
  );
}

describe("FilesTab", () => {
  beforeEach(() => {
    getMock.mockReset();
    getMock.mockResolvedValue(undefined);
    uploadFileMock.mockReset();
  });

  // R13c: Download/Delete/View-in-3D moved into the shared `FileActionsMenu`
  // dropdown (folded out of FilesTab's own per-row buttons) -- opening the
  // menu first (`pointerDown` on its trigger, same convention as
  // `OpenInSlicerButton.test.tsx`) is required before these actions are
  // reachable by role.
  function openActionsMenu(file: FileOut) {
    fireEvent.pointerDown(screen.getByRole("button", { name: `Actions for ${file.rel_path}` }), { button: 0 });
  }

  it("keeps the download action enabled and linked for a verified file", async () => {
    renderFilesTab([VERIFIED_FILE]);
    openActionsMenu(VERIFIED_FILE);

    const downloadLink = await screen.findByRole("menuitem", { name: `Download ${VERIFIED_FILE.rel_path}` });
    expect(downloadLink).toHaveAttribute("href", `/api/files/${VERIFIED_FILE.id}/download`);
  });

  it("disables the download action for a file still processing (verified_at === null)", async () => {
    renderFilesTab([PROCESSING_FILE]);
    openActionsMenu(PROCESSING_FILE);

    const downloadItem = await screen.findByRole("menuitem", { name: `Download ${PROCESSING_FILE.rel_path}` });
    expect(downloadItem).toHaveAttribute("aria-disabled", "true");
    expect(downloadItem).toHaveAttribute("title", expect.stringMatching(/processing/i));

    expect(screen.getByText("processing")).toBeInTheDocument();
  });

  it("renders a thumbnail image when the file's thumb is ready", () => {
    renderFilesTab([{ ...VERIFIED_FILE, thumb_ready: true }]);

    const thumb = screen.getByRole("img");
    expect(thumb).toHaveAttribute("src", `/api/blobs/${VERIFIED_FILE.blob_hash}/thumb?size=256`);
  });

  it("falls back to a format icon when the thumb isn't ready, and again after an image load error", () => {
    const { container } = renderFilesTab([{ ...VERIFIED_FILE, thumb_ready: true }]);

    const thumb = screen.getByRole("img");
    fireEvent.error(thumb);

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(container.querySelector("svg")).toBeInTheDocument();
  });

  it("renders a format icon (no image) when the file has no thumb at all", () => {
    renderFilesTab([{ ...VERIFIED_FILE, thumb_ready: false }]);

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("shows a mesh meta line with tris, dims, and volume, skipping null parts", () => {
    renderFilesTab([
      {
        ...VERIFIED_FILE,
        kind: "mesh",
        meta: {
          triangle_count: 1234,
          dims_mm: [10, 20.25, 30],
          volume_cm3: 5.5,
          surface_area_cm2: null,
          is_watertight: true,
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
        },
      },
    ]);

    expect(screen.getByText("1234 tris · 10.0 × 20.3 × 30.0 mm · 5.5 cm³")).toBeInTheDocument();
  });

  it("shows a mesh meta line with only the parts that have data", () => {
    renderFilesTab([
      {
        ...VERIFIED_FILE,
        kind: "cad",
        meta: {
          triangle_count: null,
          dims_mm: null,
          volume_cm3: 12,
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
        },
      },
    ]);

    expect(screen.getByText("12.0 cm³")).toBeInTheDocument();
  });

  it("shows a sliced meta line with plate count, print time, and filament weight", () => {
    renderFilesTab([
      {
        ...VERIFIED_FILE,
        format: "gcode_3mf",
        kind: "sliced",
        meta: {
          triangle_count: null,
          dims_mm: null,
          volume_cm3: null,
          surface_area_cm2: null,
          is_watertight: null,
          print_time_s: 5400,
          filament_g: 42.4,
          filament_m: null,
          filament_types: null,
          layer_height: 0.2,
          nozzle: 0.4,
          printer_model: "A1 mini",
          plate_count: 2,
          layer_count: null,
          infill_pct: null,
          slicer: null,
          plates: null,
        },
      },
    ]);

    expect(screen.getByText("2 plates · 1h 30m · 42 g")).toBeInTheDocument();
  });

  it("shows no meta line when the file has no metadata yet", () => {
    renderFilesTab([{ ...VERIFIED_FILE, meta: null }]);

    expect(screen.queryByTitle(/tris|plates/)).not.toBeInTheDocument();
  });

  const glbFile: FileOut = { ...VERIFIED_FILE, glb_status: "ok" };
  const cadPending: FileOut = {
    ...VERIFIED_FILE,
    id: 3,
    rel_path: "part.step",
    format: "step",
    kind: "cad",
    glb_status: "pending",
  };
  const notViewable: FileOut = {
    ...VERIFIED_FILE,
    id: 4,
    rel_path: "readme.txt",
    format: "step",
    kind: "cad",
    glb_status: null,
  };

  it("omits View in 3D for every file when onViewIn3D isn't passed", async () => {
    renderFilesTab([glbFile, cadPending, notViewable]);
    openActionsMenu(glbFile);
    await screen.findByRole("menuitem", { name: `Download ${glbFile.rel_path}` });
    expect(screen.queryByRole("menuitem", { name: /View .* in 3D/ })).not.toBeInTheDocument();
  });

  it("shows View in 3D for a glb-ready file once onViewIn3D is passed", async () => {
    renderFilesTab([glbFile, cadPending, notViewable], vi.fn());
    openActionsMenu(glbFile);
    expect(await screen.findByRole("menuitem", { name: `View ${glbFile.rel_path} in 3D` })).toBeInTheDocument();
  });

  it("shows View in 3D for a CAD file pending GLB conversion, and fires onViewIn3D with that file", async () => {
    const onViewIn3D = vi.fn();
    renderFilesTab([glbFile, cadPending, notViewable], onViewIn3D);
    openActionsMenu(cadPending);

    fireEvent.click(await screen.findByRole("menuitem", { name: `View ${cadPending.rel_path} in 3D` }));
    expect(onViewIn3D).toHaveBeenCalledWith(cadPending);
  });

  it("omits View in 3D for a file that isn't studio-viewable, even when onViewIn3D is passed", async () => {
    renderFilesTab([glbFile, cadPending, notViewable], vi.fn());
    openActionsMenu(notViewable);

    await screen.findByRole("menuitem", { name: `Download ${notViewable.rel_path}` });
    expect(screen.queryByRole("menuitem", { name: `View ${notViewable.rel_path} in 3D` })).not.toBeInTheDocument();
  });

  it("keeps the compact hash copy button reachable by its accessible name", () => {
    renderFilesTab([VERIFIED_FILE]);

    expect(screen.getByRole("button", { name: `Copy hash ${VERIFIED_FILE.blob_hash}` })).toBeInTheDocument();
  });

  it("excludes doc-kind files -- they only appear in the Docs tab", () => {
    const docFile: FileOut = { ...VERIFIED_FILE, id: 5, rel_path: "README.md", format: "md", kind: "doc" };
    renderFilesTab([VERIFIED_FILE, docFile]);

    expect(screen.getByText(VERIFIED_FILE.rel_path)).toBeInTheDocument();
    expect(screen.queryByText(docFile.rel_path)).not.toBeInTheDocument();
  });

  it("hides the Print button for a sliced file when the printer feature is off (default mock)", async () => {
    const slicedFile: FileOut = { ...VERIFIED_FILE, format: "gcode_3mf", kind: "sliced" };
    renderFilesTab([slicedFile]);

    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/features"));
    expect(screen.queryByRole("button", { name: `Print ${slicedFile.rel_path}` })).not.toBeInTheDocument();
  });

  it("shows the Print button for a sliced file once the printer feature is on and a printer exists", async () => {
    const slicedFile: FileOut = { ...VERIFIED_FILE, format: "gcode_3mf", kind: "sliced" };
    const printer: PrinterOut = {
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
    const features: Features = { printer_enabled: true, watch_dir: null, watch_enabled: false };
    getMock.mockImplementation((path: string) => {
      if (path === "/features") return Promise.resolve(features);
      if (path === "/printers") return Promise.resolve([printer]);
      return Promise.resolve([]);
    });

    renderFilesTab([slicedFile]);

    expect(await screen.findByRole("button", { name: `Print ${slicedFile.rel_path}` })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// "Add files" action (Task 10): uploads straight into the model's CURRENT
// revision via the shared UploadDropzone unit -- no model search, no
// `create_revision` call.
// ---------------------------------------------------------------------------

function selectFileForUpload(name: string): File {
  const input = document.querySelector('input[type="file"]');
  if (!(input instanceof HTMLInputElement)) throw new Error("file input not found");
  const file = new File(["bytes"], name);
  fireEvent.change(input, { target: { files: [file] } });
  return file;
}

describe("FilesTab -- Add files", () => {
  beforeEach(() => {
    getMock.mockReset();
    getMock.mockResolvedValue(undefined);
    uploadFileMock.mockReset();
  });

  it("uploads a selected file straight to the model's current revision, with no model search step", async () => {
    uploadFileMock.mockResolvedValueOnce({ file_id: 1, blob_hash: "abc123", size: 5, job_id: "job-1" });
    const model = buildModel([]);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={queryClient}>
        <FilesTab model={model} />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Add files" }));
    const file = selectFileForUpload("part.stl");
    fireEvent.click(screen.getByRole("button", { name: /^Upload/ }));

    await waitFor(() => expect(uploadFileMock).toHaveBeenCalledTimes(1));
    expect(uploadFileMock.mock.calls[0][0]).toEqual({
      modelId: model.id,
      revisionId: model.current_revision!.id,
      relPath: file.name,
      file,
    });

    // No "new vs existing model" target-resolution step anywhere in this flow.
    expect(screen.queryByLabelText("Model name")).not.toBeInTheDocument();
    expect(screen.queryByText(/existing model/i)).not.toBeInTheDocument();
  });

  it("invalidates the model detail query (not the gallery-only list) once the batch finishes", async () => {
    uploadFileMock.mockResolvedValueOnce({ file_id: 1, blob_hash: "abc123", size: 5, job_id: "job-1" });
    const model = buildModel([]);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    render(
      <QueryClientProvider client={queryClient}>
        <FilesTab model={model} />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Add files" }));
    selectFileForUpload("part.stl");
    fireEvent.click(screen.getByRole("button", { name: /^Upload/ }));

    await waitFor(() => expect(uploadFileMock).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: modelQueryOptions(model.slug).queryKey }),
    );
  });

  it("hides the Add files action when the model has no current revision", () => {
    const model = buildModel([]);
    const noRevisionModel: ModelDetail = { ...model, current_revision: null };
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={queryClient}>
        <FilesTab model={noRevisionModel} />
      </QueryClientProvider>,
    );

    expect(screen.queryByRole("button", { name: "Add files" })).not.toBeInTheDocument();
  });
});
