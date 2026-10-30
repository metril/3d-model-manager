import type { ReactNode } from "react";

/** One file/doc row. Width-agnostic: every layout switch is a container
 * query on the enclosing `@container/files` list, never a viewport query,
 * so it stays correct when the hosting column is resized. */
export function FileRow({
  thumb,
  path,
  pathTitle,
  meta,
  facts,
  actions,
  below,
  leading,
}: {
  thumb: ReactNode;
  /** Optional control (e.g. a select checkbox) shown before the path. */
  leading?: ReactNode;
  path: string;
  pathTitle?: string;
  /** Optional secondary line directly under the path (mesh/sliced stats). */
  meta?: string | null;
  /** Wrapping facts line: size, date, status, hash. */
  facts: ReactNode;
  actions: ReactNode;
  /** Full-width slot under the row (e.g. a doc preview). */
  below?: ReactNode;
}) {
  return (
    <li className="grid grid-cols-[2.5rem_minmax(0,1fr)_auto] gap-x-3 gap-y-1 py-3">
      <div className="row-span-2 self-center">{thumb}</div>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          {leading}
          <div className="min-w-0 truncate font-mono text-xs" title={pathTitle ?? path}>
            {path}
          </div>
        </div>
        {meta ? (
          <div className="truncate text-[11px] text-muted-foreground" title={meta}>
            {meta}
          </div>
        ) : null}
      </div>
      <div className="col-start-3 row-span-2 row-start-1 flex items-center gap-1 self-center">{actions}</div>
      <div className="col-start-2 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {facts}
      </div>
      {below ? <div className="col-span-3 min-w-0 pt-2 [overflow-wrap:anywhere]">{below}</div> : null}
    </li>
  );
}
