import { describe, expect, it } from "vitest";

import {
  applyFilters,
  breadcrumbs,
  buildTree,
  countByGroup,
  findNode,
  flattenFiles,
  sortFiles,
  typeGroupOf,
} from "@/components/model-detail/explorer/tree";
import { EMPTY_TAG_FILTER } from "@/components/tags/tagFilter";
import type { FileOut } from "@/api/types";

let nextId = 1;
function f(rel_path: string, overrides: Partial<FileOut> = {}): FileOut {
  return {
    id: nextId++,
    revision_id: 1,
    rel_path,
    storage_path: `/data/${rel_path}`,
    blob_hash: `hash${nextId}`,
    size: 100,
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

const FILES = [
  f("a.stl"),
  f("parts/b.stl"),
  f("parts/inner/c.3mf", { format: "3mf" }),
  f("docs/readme.md", { format: "md", kind: "doc" }),
  f("photos/p.png", { format: "png", kind: "image" }),
  f("zz/clip.mp4", { format: "mp4", kind: "video" }),
];

describe("buildTree", () => {
  it("nests files under folders derived from rel_path", () => {
    const root = buildTree(FILES);
    expect(root.files.map((x) => x.rel_path)).toEqual(["a.stl"]);
    expect(root.folders.map((x) => x.name)).toEqual(["docs", "parts", "photos", "zz"]);
    const parts = findNode(root, "parts");
    expect(parts?.files.map((x) => x.rel_path)).toEqual(["parts/b.stl"]);
    expect(parts?.folders.map((x) => x.path)).toEqual(["parts/inner"]);
  });

  it("counts files recursively per folder", () => {
    const root = buildTree(FILES);
    expect(root.total).toBe(6);
    expect(findNode(root, "parts")?.total).toBe(2);
    expect(findNode(root, "parts/inner")?.total).toBe(1);
  });

  it("returns an empty root for no files and null for unknown paths", () => {
    const root = buildTree([]);
    expect(root.total).toBe(0);
    expect(root.folders).toEqual([]);
    expect(findNode(buildTree(FILES), "nope/deeper")).toBeNull();
  });

  it("flattens a subtree depth-first", () => {
    const parts = findNode(buildTree(FILES), "parts")!;
    expect(flattenFiles(parts).map((x) => x.rel_path)).toEqual(["parts/b.stl", "parts/inner/c.3mf"]);
  });
});

describe("breadcrumbs", () => {
  it("starts at the root and adds one crumb per segment", () => {
    expect(breadcrumbs("")).toEqual([{ name: "", path: "" }]);
    expect(breadcrumbs("parts/inner")).toEqual([
      { name: "", path: "" },
      { name: "parts", path: "parts" },
      { name: "inner", path: "parts/inner" },
    ]);
  });
});

describe("type groups and filters", () => {
  it("maps kinds to chip groups", () => {
    expect(["mesh", "cad", "sliced", "gcode"].map((k) => typeGroupOf(k as FileOut["kind"]))).toEqual([
      "models",
      "models",
      "models",
      "models",
    ]);
    expect(typeGroupOf("image")).toBe("images");
    expect(typeGroupOf("doc")).toBe("docs");
    expect(typeGroupOf("video")).toBe("misc");
    expect(typeGroupOf("other")).toBe("misc");
  });

  it("counts files per group", () => {
    expect(countByGroup(FILES)).toEqual({ all: 6, models: 3, images: 1, docs: 1, misc: 1 });
  });

  it("filters by type and by case-insensitive search over the path", () => {
    const base = { query: "", tagFilter: EMPTY_TAG_FILTER };
    expect(applyFilters(FILES, { ...base, type: "docs" }).map((x) => x.rel_path)).toEqual(["docs/readme.md"]);
    expect(applyFilters(FILES, { ...base, type: "all", query: "PARTS/" })).toHaveLength(2);
    expect(applyFilters(FILES, { ...base, type: "images", query: "stl" })).toHaveLength(0);
  });

  it("filters by tag", () => {
    const tagged = f("t.stl", { tag_refs: [{ id: 7, name: "x", color: null }] as FileOut["tag_refs"] });
    const out = applyFilters([tagged, f("u.stl")], { query: "", type: "all", tagFilter: { tags: [7], mode: "any" } });
    expect(out.map((x) => x.rel_path)).toEqual(["t.stl"]);
  });
});

describe("sortFiles", () => {
  const set = [
    f("b.stl", { size: 5, mtime: "2026-01-02T00:00:00Z", format: "stl" }),
    f("a10.stl", { size: 50, mtime: "2026-01-01T00:00:00Z", format: "obj" }),
    f("a2.stl", { size: 20, mtime: "2026-01-03T00:00:00Z", format: "3mf" }),
  ];
  const names = (x: FileOut[]) => x.map((y) => y.rel_path);

  it("sorts by name (natural), size, mtime and type, in both directions", () => {
    expect(names(sortFiles(set, "name", "asc"))).toEqual(["a2.stl", "a10.stl", "b.stl"]);
    expect(names(sortFiles(set, "name", "desc"))).toEqual(["b.stl", "a10.stl", "a2.stl"]);
    expect(names(sortFiles(set, "size", "desc"))).toEqual(["a10.stl", "a2.stl", "b.stl"]);
    expect(names(sortFiles(set, "mtime", "asc"))).toEqual(["a10.stl", "b.stl", "a2.stl"]);
    expect(names(sortFiles(set, "type", "asc"))).toEqual(["a2.stl", "a10.stl", "b.stl"]);
  });

  it("does not mutate its input", () => {
    const before = names(set);
    sortFiles(set, "size", "asc");
    expect(names(set)).toEqual(before);
  });
});
