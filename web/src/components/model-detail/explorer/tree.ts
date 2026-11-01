/** Pure helpers behind the file explorer: folder tree, type groups, filters
 * and sorting over a revision's flat `rel_path` list. No React, no I/O. */
import type { BlobKind, FileOut } from "@/api/types";
import { matchesTagFilter, type TagFilterValue } from "@/components/tags/tagFilter";

export interface TreeNode {
  /** Last path segment ("" for the root). */
  name: string;
  /** Slash-joined path from the root ("" for the root). */
  path: string;
  folders: TreeNode[];
  /** Files directly inside this folder. */
  files: FileOut[];
  /** Files in this folder and every descendant. */
  total: number;
}

export function splitPath(path: string): string[] {
  return path.split("/").filter((segment) => segment.length > 0);
}

export function baseName(path: string): string {
  return splitPath(path).at(-1) ?? path;
}

export function dirName(path: string): string {
  return splitPath(path).slice(0, -1).join("/");
}

export function isInside(path: string, folder: string): boolean {
  return folder === "" || path === folder || path.startsWith(`${folder}/`);
}

/** Builds the folder tree from `rel_path`s. Folders sort by name; files keep
 * their input order (sorting is the caller's concern). */
export function buildTree(files: FileOut[]): TreeNode {
  const root: TreeNode = { name: "", path: "", folders: [], files: [], total: 0 };
  const index = new Map<string, TreeNode>([["", root]]);
  for (const file of files) {
    const segments = splitPath(file.rel_path);
    let node = root;
    node.total += 1;
    for (let i = 0; i < segments.length - 1; i += 1) {
      const path = segments.slice(0, i + 1).join("/");
      let child = index.get(path);
      if (!child) {
        child = { name: segments[i], path, folders: [], files: [], total: 0 };
        index.set(path, child);
        node.folders.push(child);
      }
      child.total += 1;
      node = child;
    }
    node.files.push(file);
  }
  const sortFolders = (node: TreeNode) => {
    node.folders.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    node.folders.forEach(sortFolders);
  };
  sortFolders(root);
  return root;
}

export function findNode(root: TreeNode, path: string): TreeNode | null {
  let node = root;
  for (const segment of splitPath(path)) {
    const next = node.folders.find((folder) => folder.name === segment);
    if (!next) return null;
    node = next;
  }
  return node;
}

/** Breadcrumb trail for `path`, starting with the root (empty name/path). */
export function breadcrumbs(path: string): { name: string; path: string }[] {
  const segments = splitPath(path);
  return [{ name: "", path: "" }, ...segments.map((name, i) => ({ name, path: segments.slice(0, i + 1).join("/") }))];
}

/** Every file in `node` and its descendants, depth-first. */
export function flattenFiles(node: TreeNode): FileOut[] {
  return [...node.files, ...node.folders.flatMap(flattenFiles)];
}

export type TypeGroup = "models" | "images" | "docs" | "misc";
export type TypeFilter = TypeGroup | "all";

export const TYPE_GROUPS: TypeGroup[] = ["models", "images", "docs", "misc"];

export function typeGroupOf(kind: BlobKind): TypeGroup {
  switch (kind) {
    case "mesh":
    case "cad":
    case "sliced":
    case "gcode":
      return "models";
    case "image":
      return "images";
    case "doc":
      return "docs";
    default:
      return "misc";
  }
}

export function countByGroup(files: FileOut[]): Record<TypeFilter, number> {
  const counts: Record<TypeFilter, number> = { all: files.length, models: 0, images: 0, docs: 0, misc: 0 };
  for (const file of files) counts[typeGroupOf(file.kind)] += 1;
  return counts;
}

export interface ExplorerFilters {
  query: string;
  tagFilter: TagFilterValue;
  type: TypeFilter;
}

export function matchesQuery(file: FileOut, query: string): boolean {
  const q = query.trim().toLowerCase();
  return q === "" || file.rel_path.toLowerCase().includes(q);
}

/** Query + tag filter only (what the type-chip counts are computed over). */
export function applySearch(files: FileOut[], filters: Pick<ExplorerFilters, "query" | "tagFilter">): FileOut[] {
  return files.filter((file) => matchesQuery(file, filters.query) && matchesTagFilter(file.tag_refs, filters.tagFilter));
}

export function applyFilters(files: FileOut[], filters: ExplorerFilters): FileOut[] {
  return applySearch(files, filters).filter((file) => filters.type === "all" || typeGroupOf(file.kind) === filters.type);
}

export type SortKey = "name" | "size" | "mtime" | "type";
export type SortDir = "asc" | "desc";

export function sortFiles(files: FileOut[], key: SortKey, dir: SortDir): FileOut[] {
  const sign = dir === "asc" ? 1 : -1;
  const byName = (a: FileOut, b: FileOut) =>
    baseName(a.rel_path).localeCompare(baseName(b.rel_path), undefined, { numeric: true, sensitivity: "base" });
  const compare = (a: FileOut, b: FileOut): number => {
    switch (key) {
      case "size":
        return a.size - b.size;
      case "mtime":
        return (a.mtime ? Date.parse(a.mtime) : 0) - (b.mtime ? Date.parse(b.mtime) : 0);
      case "type":
        return (a.format ?? "").localeCompare(b.format ?? "") || a.kind.localeCompare(b.kind);
      default:
        return byName(a, b);
    }
  };
  return [...files].sort((a, b) => sign * compare(a, b) || byName(a, b));
}
