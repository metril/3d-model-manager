import { Badge } from "@/components/ui/badge";
import { humanizeBytes } from "@/lib/format";
import type { DiffEntry, DiffResponse } from "@/api/types";

type DiffKey = keyof DiffResponse;

const DIFF_SECTIONS: { key: DiffKey; label: string; badgeClassName: string }[] = [
  { key: "added", label: "Added", badgeClassName: "bg-success/15 text-success" },
  { key: "removed", label: "Removed", badgeClassName: "bg-destructive/15 text-destructive" },
  { key: "changed", label: "Changed", badgeClassName: "bg-warning/15 text-warning" },
  { key: "unchanged", label: "Unchanged", badgeClassName: "bg-muted text-muted-foreground" },
];

function entrySize(entry: DiffEntry): string {
  const side = entry.b ?? entry.a;
  return side ? humanizeBytes(side.size) : "—";
}

export function DiffView({ diff }: { diff: DiffResponse }) {
  return (
    <div className="space-y-4">
      {DIFF_SECTIONS.map(({ key, label, badgeClassName }) => {
        const entries = diff[key];
        return (
          <section key={key} aria-label={label}>
            <h4 className="mb-1.5 flex items-center gap-2 text-sm font-medium">
              {label}
              <Badge variant="outline" className={badgeClassName}>
                {entries.length}
              </Badge>
            </h4>
            {entries.length === 0 ? (
              <p className="text-xs text-muted-foreground">None</p>
            ) : (
              <ul className="divide-y divide-border rounded-md border border-border">
                {entries.map((entry) => (
                  <li key={entry.rel_path} className="flex items-center justify-between gap-2 px-2.5 py-1.5">
                    <span className="truncate font-mono text-xs">{entry.rel_path}</span>
                    <span className="flex shrink-0 items-center gap-2">
                      <span className="text-xs text-muted-foreground">{entrySize(entry)}</span>
                      <Badge variant="outline" className={badgeClassName}>
                        {label}
                      </Badge>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}
