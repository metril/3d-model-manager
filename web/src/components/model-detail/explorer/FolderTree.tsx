import { useState, type ReactNode } from "react";
import { ChevronRightIcon, FolderIcon, FolderOpenIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { isInside, type TreeNode } from "@/components/model-detail/explorer/tree";
import { cn } from "@/lib/utils";

function Branch({
  node,
  depth,
  current,
  onSelect,
  folderSlot,
}: {
  node: TreeNode;
  depth: number;
  current: string;
  onSelect: (path: string) => void;
  folderSlot?: (path: string) => ReactNode;
}) {
  // Ancestors of the current folder open automatically; the user can still
  // toggle any branch by hand.
  const { t } = useTranslation();
  const [manual, setManual] = useState<boolean | null>(null);
  const expanded = manual ?? (node.path === "" || isInside(current, node.path));
  const hasChildren = node.folders.length > 0;
  const active = current === node.path;
  const Icon = active || expanded ? FolderOpenIcon : FolderIcon;

  return (
    <li role="treeitem" aria-expanded={hasChildren ? expanded : undefined} aria-selected={active}>
      <div
        className={cn("group flex items-center gap-1 rounded pr-1 text-sm hover:bg-muted", active && "bg-muted font-medium")}
        style={{ paddingLeft: `${depth * 0.75}rem` }}
      >
        <button
          type="button"
          tabIndex={-1}
          aria-label={t(expanded ? "explorer.collapse" : "explorer.expand", { name: node.name })}
          className={cn("flex size-5 shrink-0 items-center justify-center text-muted-foreground", !hasChildren && "invisible")}
          onClick={() => setManual(!expanded)}
        >
          <ChevronRightIcon className={cn("size-3.5 transition-transform", expanded && "rotate-90")} />
        </button>
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-1.5 py-1.5 text-left"
          onClick={() => onSelect(node.path)}
        >
          <Icon className="size-4 shrink-0 text-primary/70" />
          <span className="flex-1 truncate">{node.name || "/"}</span>
          <span className="text-xs text-muted-foreground tabular-nums">{node.total}</span>
        </button>
        {folderSlot ? <span className="shrink-0">{folderSlot(node.path)}</span> : null}
      </div>
      {hasChildren && expanded ? (
        <ul role="group">
          {node.folders.map((child) => (
            <Branch key={child.path} node={child} depth={depth + 1} current={current} onSelect={onSelect} folderSlot={folderSlot} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/** Folder-tree sidebar over the full (unfiltered) revision tree. `folderSlot`
 * renders per-folder actions (PR 4: rename, move, drop targets). */
export function FolderTree({
  root,
  current,
  onSelect,
  rootLabel,
  folderSlot,
}: {
  root: TreeNode;
  current: string;
  onSelect: (path: string) => void;
  rootLabel: string;
  folderSlot?: (path: string) => ReactNode;
}) {
  return (
    <ul role="tree" aria-label={rootLabel} className="space-y-0.5">
      <Branch node={{ ...root, name: rootLabel }} depth={0} current={current} onSelect={onSelect} folderSlot={folderSlot} />
    </ul>
  );
}
