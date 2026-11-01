import { useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { ChevronRightIcon, FolderIcon, FolderOpenIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { isInside, type TreeNode } from "@/components/model-detail/explorer/tree";
import { cn } from "@/lib/utils";

interface BranchProps {
  node: TreeNode;
  depth: number;
  current: string;
  tabStop: string;
  isExpanded: (node: TreeNode) => boolean;
  setExpanded: (node: TreeNode, open: boolean) => void;
  onFocusItem: (path: string) => void;
  onSelect: (path: string) => void;
  folderSlot?: (path: string) => ReactNode;
}

function Branch({ node, depth, current, tabStop, isExpanded, setExpanded, onFocusItem, onSelect, folderSlot }: BranchProps) {
  const { t } = useTranslation();
  const hasChildren = node.folders.length > 0;
  const expanded = isExpanded(node);
  const active = current === node.path;
  const Icon = active || expanded ? FolderOpenIcon : FolderIcon;

  return (
    <li
      role="treeitem"
      data-tree-path={node.path}
      aria-expanded={hasChildren ? expanded : undefined}
      aria-selected={active}
      aria-level={depth + 1}
      tabIndex={tabStop === node.path ? 0 : -1}
      onFocus={(event) => {
        if (event.target === event.currentTarget) onFocusItem(node.path);
      }}
      className="outline-none focus-visible:[&>div]:ring-2 focus-visible:[&>div]:ring-ring"
    >
      <div
        className={cn("group flex items-center gap-1 rounded pr-1 text-sm hover:bg-muted", active && "bg-muted font-medium")}
        style={{ paddingLeft: `${depth * 0.75}rem` }}
      >
        <button
          type="button"
          tabIndex={-1}
          aria-label={t(expanded ? "explorer.collapse" : "explorer.expand", { name: node.name })}
          className={cn("flex size-5 shrink-0 items-center justify-center text-muted-foreground", !hasChildren && "invisible")}
          onClick={() => setExpanded(node, !expanded)}
        >
          <ChevronRightIcon className={cn("size-3.5 transition-transform", expanded && "rotate-90")} />
        </button>
        <button
          type="button"
          tabIndex={-1}
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
            <Branch
              key={child.path}
              node={child}
              depth={depth + 1}
              current={current}
              tabStop={tabStop}
              isExpanded={isExpanded}
              setExpanded={setExpanded}
              onFocusItem={onFocusItem}
              onSelect={onSelect}
              folderSlot={folderSlot}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/** Folder-tree sidebar over the full (unfiltered) revision tree, following the
 * WAI-ARIA tree pattern: one roving tab stop; Up/Down move, Right/Left
 * expand/collapse (or step into/out of a branch), Home/End jump, Enter/Space
 * open the folder. `folderSlot` renders per-folder actions (PR 4). */
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
  const listRef = useRef<HTMLUListElement>(null);
  // Ancestors of the current folder open automatically; the user can still
  // toggle any branch by hand.
  const [manual, setManual] = useState<Record<string, boolean>>({});
  const [focusPath, setFocusPath] = useState(current);
  const isExpanded = (node: TreeNode) => manual[node.path] ?? (node.path === "" || isInside(current, node.path));
  const setExpanded = (node: TreeNode, open: boolean) => setManual((prev) => ({ ...prev, [node.path]: open }));
  const top: TreeNode = { ...root, name: rootLabel };

  // Visible items in DOM order, with parents, for arrow navigation.
  const visible: { node: TreeNode; parent: TreeNode | null }[] = [];
  const walk = (node: TreeNode, parent: TreeNode | null) => {
    visible.push({ node, parent });
    if (isExpanded(node)) node.folders.forEach((child) => walk(child, node));
  };
  walk(top, null);
  const tabStop = visible.some((item) => item.node.path === focusPath) ? focusPath : current;

  function focusPathItem(path: string) {
    setFocusPath(path);
    listRef.current
      ?.querySelectorAll<HTMLElement>("[data-tree-path]")
      .forEach((el) => {
        if (el.dataset.treePath === path) el.focus();
      });
  }

  function onKeyDown(event: KeyboardEvent<HTMLUListElement>) {
    const item = (event.target as HTMLElement).closest<HTMLElement>('[role="treeitem"]');
    if (!item || item !== event.target) return;
    const index = visible.findIndex((entry) => entry.node.path === item.dataset.treePath);
    if (index < 0) return;
    const { node, parent } = visible[index];
    const hasChildren = node.folders.length > 0;
    let handled = true;
    switch (event.key) {
      case "ArrowDown":
        if (index < visible.length - 1) focusPathItem(visible[index + 1].node.path);
        break;
      case "ArrowUp":
        if (index > 0) focusPathItem(visible[index - 1].node.path);
        break;
      case "ArrowRight":
        if (hasChildren && !isExpanded(node)) setExpanded(node, true);
        else if (hasChildren) focusPathItem(node.folders[0].path);
        break;
      case "ArrowLeft":
        if (hasChildren && isExpanded(node)) setExpanded(node, false);
        else if (parent) focusPathItem(parent.path);
        break;
      case "Home":
        focusPathItem(visible[0].node.path);
        break;
      case "End":
        focusPathItem(visible[visible.length - 1].node.path);
        break;
      case "Enter":
      case " ":
        onSelect(node.path);
        break;
      default:
        handled = false;
    }
    if (handled) event.preventDefault();
  }

  return (
    <ul ref={listRef} role="tree" aria-label={rootLabel} className="space-y-0.5" onKeyDown={onKeyDown}>
      <Branch
        node={top}
        depth={0}
        current={current}
        tabStop={tabStop}
        isExpanded={isExpanded}
        setExpanded={setExpanded}
        onFocusItem={setFocusPath}
        onSelect={onSelect}
        folderSlot={folderSlot}
      />
    </ul>
  );
}
