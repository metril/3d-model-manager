import { TagIcon, XIcon } from "lucide-react";

import { useTags } from "@/api/tags";
import { TagChip } from "@/components/tags/TagChip";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { EMPTY_TAG_FILTER, type TagFilterValue } from "@/components/tags/tagFilter";
import { cn } from "@/lib/utils";
import type { TagCounts } from "@/api/types";

/** Multi-select tag filter with an any/all toggle and per-tag usage counts.
 * `countKey` picks which usage count is shown next to each tag. */
export function TagFilter({
  value,
  onChange,
  label = "Tags",
  countKey = "total",
}: {
  value: TagFilterValue;
  onChange: (next: TagFilterValue) => void;
  label?: string;
  countKey?: keyof TagCounts;
}) {
  const tagsQuery = useTags();
  const tags = tagsQuery.data ?? [];
  const selected = new Set(value.tags);

  function toggle(id: number) {
    const next = selected.has(id) ? value.tags.filter((t) => t !== id) : [...value.tags, id];
    onChange({ ...value, tags: next });
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button type="button" variant={value.tags.length ? "secondary" : "outline"} size="sm">
          <TagIcon /> {value.tags.length ? `${label} (${value.tags.length})` : label}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 p-2">
        {tags.length === 0 ? (
          <p className="px-1 py-0.5 text-xs text-muted-foreground">No tags yet</p>
        ) : (
          <div className="space-y-2">
            <ul className="max-h-60 space-y-0.5 overflow-y-auto" aria-label={`${label} filter`}>
              {tags.map((tag) => (
                <li key={tag.id}>
                  <button
                    type="button"
                    aria-pressed={selected.has(tag.id)}
                    onClick={() => toggle(tag.id)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded px-2 py-1 text-left hover:bg-muted",
                      selected.has(tag.id) && "bg-muted",
                    )}
                  >
                    <TagChip tag={tag} />
                    <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                      {tag.counts?.[countKey] ?? 0}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {value.tags.length > 0 && (
              <div className="flex items-center justify-between gap-2 border-t pt-2">
                {value.tags.length > 1 ? (
                  <div className="inline-flex rounded-md border p-0.5" role="group" aria-label="Match mode">
                    {(["any", "all"] as const).map((mode) => (
                      <Button
                        key={mode}
                        type="button"
                        size="xs"
                        variant={value.mode === mode ? "secondary" : "ghost"}
                        aria-pressed={value.mode === mode}
                        onClick={() => onChange({ ...value, mode })}
                      >
                        {mode === "any" ? "Any" : "All"}
                      </Button>
                    ))}
                  </div>
                ) : (
                  <span />
                )}
                <Button type="button" size="xs" variant="ghost" onClick={() => onChange(EMPTY_TAG_FILTER)}>
                  <XIcon /> Clear
                </Button>
              </div>
            )}
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
