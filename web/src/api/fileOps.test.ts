import { describe, expect, it } from "vitest";

import {
  isMoveTargetDisabled,
  planMoveInto,
  planRelocation,
  planRenameFile,
  planRenameFolder,
  validateName,
} from "@/api/fileOps";
import { decodeDrag, dragItemsFor, encodeDrag, isExplorerDrag, itemsFromKeys, DND_TYPE } from "@/components/model-detail/explorer/dnd";
import { buildTree } from "@/components/model-detail/explorer/tree";
import { childNames, pruneEmptyFolders, rewriteFolders, withEmptyFolders } from "@/components/model-detail/explorer/treeEdit";
import type { FileOut } from "@/api/types";

const f = (id: number, rel_path: string) => ({ id, rel_path }) as FileOut;
const FILES = [f(1, "a.stl"), f(2, "parts/b.stl"), f(3, "parts/deep/c.stl"), f(4, "docs/readme.md")];

describe("validateName", () => {
  it("rejects empty, slashes, dots, _snapshots and case-insensitive clashes", () => {
    expect(validateName("  ", [])).toBe("empty");
    expect(validateName("a/b", [])).toBe("slash");
    expect(validateName("..", [])).toBe("dot");
    expect(validateName(".", [])).toBe("dot");
    expect(validateName("_Snapshots", [])).toBe("reserved");
    expect(validateName("PARTS", ["parts"])).toBe("clash");
    expect(validateName("ok.stl", ["a.stl"])).toBeNull();
  });
});

describe("rename plans", () => {
  it("renames one file in place", () => {
    const plan = planRenameFile(FILES, FILES[1], "z.stl");
    expect(plan.moves).toEqual([{ file_id: 2, rel_path: "parts/z.stl" }]);
  });

  it("rewrites the prefix of every descendant when renaming a folder", () => {
    const plan = planRenameFolder(FILES, "parts", "bits");
    expect(plan.error).toBeNull();
    expect(plan.moves).toEqual([
      { file_id: 2, rel_path: "bits/b.stl" },
      { file_id: 3, rel_path: "bits/deep/c.stl" },
    ]);
    expect(plan.folderMoves).toEqual([{ from: "parts", to: "bits" }]);
  });

  it("reports a clash with an unmoved file (case-insensitive)", () => {
    const plan = planRenameFile([...FILES, f(5, "parts/Z.stl")], FILES[1], "z.stl");
    expect(plan.error).toBe("clash");
  });
});

describe("planMoveInto", () => {
  it("moves files and folders into a destination keeping names", () => {
    const plan = planMoveInto(FILES, { fileIds: [1], folders: ["parts/deep"] }, "docs");
    expect(plan.moves).toEqual([
      { file_id: 1, rel_path: "docs/a.stl" },
      { file_id: 3, rel_path: "docs/deep/c.stl" },
    ]);
  });

  it("moves to the root and drops no-ops", () => {
    expect(planMoveInto(FILES, { fileIds: [1], folders: [] }, "").moves).toEqual([]);
    expect(planMoveInto(FILES, { fileIds: [2], folders: [] }, "").moves).toEqual([{ file_id: 2, rel_path: "b.stl" }]);
  });

  it("refuses to move a folder into itself or a descendant", () => {
    expect(planMoveInto(FILES, { fileIds: [], folders: ["parts"] }, "parts/deep").error).toBe("into-self");
    expect(isMoveTargetDisabled({ fileIds: [], folders: ["parts"] }, "parts/deep", FILES)).toBe(true);
    expect(isMoveTargetDisabled({ fileIds: [1], folders: [] }, "", FILES)).toBe(true);
    expect(isMoveTargetDisabled({ fileIds: [1], folders: [] }, "docs", FILES)).toBe(false);
  });

  it("detects a clash with a file already at the destination", () => {
    const plan = planMoveInto([...FILES, f(5, "docs/a.stl")], { fileIds: [1], folders: [] }, "docs");
    expect(plan.error).toBe("clash");
    expect(plan.clashPath).toBe("docs/a.stl");
  });
});

describe("prefix clashes (case-insensitive, mirrors the server)", () => {
  const base = [f(1, "a.stl"), f(2, "docs/readme.md"), f(3, "x/y.stl")];

  it("rejects a target that is an existing file's parent folder", () => {
    // moving a.stl to "docs" would make docs both a file and a folder
    const plan = planRelocation(base, new Map([[1, "DOCS"]]), new Map());
    expect(plan.error).toBe("clash");
  });

  it("rejects a path that has an existing file as an ancestor", () => {
    const plan = planRelocation(base, new Map([[3, "a.stl/y.stl"]]), new Map());
    expect(plan.error).toBe("clash");
  });

  it("catches the same clashes between files moved in one batch", () => {
    expect(planRelocation(base, new Map([[1, "z"], [3, "Z/y.stl"]]), new Map()).error).toBe("clash");
    expect(planRelocation(base, new Map([[1, "z"], [2, "z"]]), new Map()).error).toBe("clash");
  });

  it("allows non-conflicting moves, and disables the move target on a clash", () => {
    expect(planRelocation(base, new Map([[1, "x/a.stl"]]), new Map()).error).toBeNull();
    const files = [f(1, "a"), f(2, "d/a/b.stl")];
    expect(isMoveTargetDisabled({ fileIds: [1], folders: [] }, "d", files)).toBe(true);
  });
});

describe("drag payload", () => {
  it("round-trips and rejects junk", () => {
    const items = { fileIds: [1, 2], folders: ["x"] };
    expect(decodeDrag(encodeDrag(items))).toEqual(items);
    expect(decodeDrag("nope")).toBeNull();
    expect(decodeDrag(JSON.stringify({ fileIds: [], folders: [] }))).toBeNull();
  });

  it("only treats our own MIME type as an explorer drag", () => {
    expect(isExplorerDrag({ types: [DND_TYPE] } as unknown as DataTransfer)).toBe(true);
    expect(isExplorerDrag({ types: ["Files"] } as unknown as DataTransfer)).toBe(false);
    expect(isExplorerDrag(null)).toBe(false);
  });

  it("drags the whole selection when the dragged item is selected", () => {
    const selected = itemsFromKeys(new Set(["file:1", "folder:parts"]));
    expect(selected).toEqual({ fileIds: [1], folders: ["parts"] });
    expect(dragItemsFor({ kind: "file", id: 1 }, selected, (k) => k === "file:1")).toEqual(selected);
    expect(dragItemsFor({ kind: "file", id: 9 }, selected, () => false)).toEqual({ fileIds: [9], folders: [] });
    expect(dragItemsFor({ kind: "folder", path: "q" }, selected, () => false)).toEqual({ fileIds: [], folders: ["q"] });
  });
});

describe("client-only folders", () => {
  it("merge into the tree, prune once real, and follow renames", () => {
    const tree = withEmptyFolders(buildTree(FILES), ["parts/new", "fresh/inner"]);
    expect(tree.folders.map((n) => n.name)).toEqual(["docs", "fresh", "parts"]);
    expect(tree.folders[1].folders[0]).toMatchObject({ path: "fresh/inner", total: 0 });
    expect(pruneEmptyFolders(buildTree(FILES), ["parts", "fresh"])).toEqual(["fresh"]);
    expect(rewriteFolders(["parts/new", "other"], "parts", "bits")).toEqual(["bits/new", "other"]);
  });

  it("lists names directly under a parent", () => {
    expect(childNames(["a.stl", "parts/b.stl", "parts/deep/c.stl"], "parts").sort()).toEqual(["b.stl", "deep"]);
    expect(childNames(["a.stl", "parts/b.stl"], "").sort()).toEqual(["a.stl", "parts"]);
  });
});
