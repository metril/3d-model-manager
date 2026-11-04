import { describe, expect, it } from "vitest";

import { glbFiles, glbUrl, pickViewerFiles } from "@/components/viewer/viewable";
import type { BlobMetaOut, FileOut, ModelDetail } from "@/api/types";

function fakeMeta(overrides: Partial<BlobMetaOut> = {}): BlobMetaOut {
  return {
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
    ...overrides,
  };
}

function fakeFile(overrides: Partial<FileOut> = {}): FileOut {
  return {
    id: 1,
    revision_id: 1,
    rel_path: "model.stl",
    storage_path: "/data/model.stl",
    blob_hash: "abc123",
    size: 2048,
    format: "stl",
    kind: "mesh",
    mtime: "2026-06-01T12:00:00Z",
    verified_at: "2026-06-01T12:00:05Z",
    meta: null,
    thumb_ready: false,
    glb_status: null,
    glb_preview_ready: false,
    ...overrides,
  };
}

function fakeModel(files: FileOut[]): ModelDetail {
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

describe("glbUrl", () => {
  it("points at the plain glb endpoint when the preview LOD isn't ready", () => {
    const file = fakeFile({ blob_hash: "hash1", glb_preview_ready: false, meta: fakeMeta({ triangle_count: 5_000_000 }) });
    expect(glbUrl(file)).toBe("/api/blobs/hash1/glb?v=2");
  });

  it("points at the plain glb endpoint when triangle count is at or under the threshold", () => {
    const file = fakeFile({ blob_hash: "hash1", glb_preview_ready: true, meta: fakeMeta({ triangle_count: 1_500_000 }) });
    expect(glbUrl(file)).toBe("/api/blobs/hash1/glb?v=2");
  });

  it("points at the plain glb endpoint when there is no metadata at all", () => {
    const file = fakeFile({ blob_hash: "hash1", glb_preview_ready: true, meta: null });
    expect(glbUrl(file)).toBe("/api/blobs/hash1/glb?v=2");
  });

  it("requests the preview LOD once it's ready and the mesh exceeds the triangle threshold", () => {
    const file = fakeFile({
      blob_hash: "hash1",
      glb_preview_ready: true,
      meta: fakeMeta({ triangle_count: 1_500_001 }),
    });
    expect(glbUrl(file)).toBe("/api/blobs/hash1/glb?preview=true&v=2");
  });
});

describe("pickViewerFiles", () => {
  it("returns an empty array when there is no current revision", () => {
    expect(pickViewerFiles({ ...fakeModel([]), current_revision: null })).toEqual([]);
  });

  it("includes files with any glb_status, sliced files, and plain gcode, in rel_path order", () => {
    const glbOk = fakeFile({ id: 1, rel_path: "c.stl", glb_status: "ok" });
    const glbPending = fakeFile({ id: 2, rel_path: "a.3mf", glb_status: "pending" });
    const sliced = fakeFile({ id: 3, rel_path: "b.gcode.3mf", format: "gcode_3mf", kind: "sliced", glb_status: null });
    const plainGcode = fakeFile({ id: 4, rel_path: "d.gcode", format: "gcode", kind: "gcode", glb_status: null });

    const picked = pickViewerFiles(fakeModel([glbOk, glbPending, sliced, plainGcode]));

    expect(picked.map((f) => f.rel_path)).toEqual(["a.3mf", "b.gcode.3mf", "c.stl", "d.gcode"]);
  });

  it("excludes files that are neither GLB-capable, sliced, nor plain gcode", () => {
    const cover = fakeFile({ id: 1, rel_path: "cover.png", format: "png", kind: "image", glb_status: null });
    expect(pickViewerFiles(fakeModel([cover]))).toEqual([]);
  });
});

describe("glbFiles", () => {
  it("returns an empty array when there is no current revision", () => {
    expect(glbFiles({ ...fakeModel([]), current_revision: null })).toEqual([]);
  });

  it("keeps only files with a ready glb, in rel_path order", () => {
    const glbOk = fakeFile({ id: 1, rel_path: "c.stl", glb_status: "ok" });
    const glbOkToo = fakeFile({ id: 2, rel_path: "a.stl", glb_status: "ok" });
    const glbPending = fakeFile({ id: 3, rel_path: "b.stl", glb_status: "pending" });
    const glbFailed = fakeFile({ id: 4, rel_path: "e.stl", glb_status: "failed" });
    const glbUnsupported = fakeFile({ id: 5, rel_path: "f.stl", glb_status: "unsupported" });
    const sliced = fakeFile({ id: 6, rel_path: "d.gcode.3mf", format: "gcode_3mf", kind: "sliced", glb_status: null });

    const picked = glbFiles(fakeModel([glbOk, glbOkToo, glbPending, glbFailed, glbUnsupported, sliced]));

    expect(picked.map((f) => f.rel_path)).toEqual(["a.stl", "c.stl"]);
  });

  it("excludes plain gcode and sliced files even though they're viewable", () => {
    const sliced = fakeFile({ id: 1, rel_path: "a.gcode.3mf", format: "gcode_3mf", kind: "sliced", glb_status: null });
    const plainGcode = fakeFile({ id: 2, rel_path: "b.gcode", format: "gcode", kind: "gcode", glb_status: null });
    expect(glbFiles(fakeModel([sliced, plainGcode]))).toEqual([]);
  });
});
