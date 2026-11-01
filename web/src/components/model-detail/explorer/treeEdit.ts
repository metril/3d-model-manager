/** Tree helpers for file management: path joining, client-only empty folders. */
import { findNode, isInside, splitPath, type TreeNode } from "@/components/model-detail/explorer/tree";

export function joinPath(...parts: string[]): string {
  return parts.flatMap(splitPath).join("/");
}

/** Adds client-only empty folders (and any missing ancestors) to a tree. Mutates and returns `root`. */
export function withEmptyFolders(root: TreeNode, folders: readonly string[]): TreeNode {
  for (const folder of folders) {
    let node = root;
    const segments = splitPath(folder);
    segments.forEach((name, i) => {
      let child = node.folders.find((f) => f.name === name);
      if (!child) {
        child = { name, path: segments.slice(0, i + 1).join("/"), folders: [], files: [], total: 0 };
        node.folders.push(child);
        node.folders.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
      }
      node = child;
    });
  }
  return root;
}

/** Drops client-only folders that now exist in the real tree (a file landed in them). */
export function pruneEmptyFolders(real: TreeNode, folders: readonly string[]): string[] {
  return folders.filter((folder) => findNode(real, folder) === null);
}

/** Rewrites client-only folder paths after a folder moved/renamed from `from` to `to`. */
export function rewriteFolders(folders: readonly string[], from: string, to: string): string[] {
  return folders.map((folder) => (from !== "" && isInside(folder, from) ? joinPath(to, folder.slice(from.length)) : folder));
}

/** Names directly inside `parent` (files and folders) given every full path. */
export function childNames(paths: readonly string[], parent: string): string[] {
  const prefix = parent === "" ? "" : `${parent}/`;
  const names = new Set<string>();
  for (const path of paths) {
    if (!path.startsWith(prefix)) continue;
    const first = path.slice(prefix.length).split("/")[0];
    if (first) names.add(first);
  }
  return [...names];
}
