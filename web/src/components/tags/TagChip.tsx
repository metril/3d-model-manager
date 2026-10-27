import { XIcon } from "lucide-react";

import { tagIconFor } from "@/components/tags/tagIcons";
import { Badge } from "@/components/ui/badge";
import { tagChipStyle } from "@/lib/tagColors";
import { cn } from "@/lib/utils";
import type { TagRef } from "@/api/types";

/** A tag as a pill: tinted by its hex color, optional lucide icon, optional
 * remove button. Uncolored tags fall back to the neutral secondary badge. */
export function TagChip({
  tag,
  onRemove,
  className,
}: {
  tag: Pick<TagRef, "name" | "color" | "icon" | "description">;
  onRemove?: () => void;
  className?: string;
}) {
  const Icon = tagIconFor(tag.icon);
  return (
    <Badge
      variant="secondary"
      title={tag.description ?? undefined}
      style={tagChipStyle(tag.color)}
      className={cn("max-w-full", tag.color && "border", className)}
      data-testid="tag-chip"
    >
      {Icon && <Icon aria-hidden />}
      <span className="truncate">{tag.name}</span>
      {onRemove && (
        <button
          type="button"
          aria-label={`Remove tag ${tag.name}`}
          onClick={(event) => {
            event.stopPropagation();
            onRemove();
          }}
          className="-mr-1 rounded-full opacity-70 hover:opacity-100"
        >
          <XIcon />
        </button>
      )}
    </Badge>
  );
}

/** Wrapped row of chips, capped at `max` with a "+N" overflow. */
export function TagChips({ tags, max, className }: { tags: Pick<TagRef, "name" | "color" | "icon" | "description">[]; max?: number; className?: string }) {
  if (tags.length === 0) return null;
  const shown = max ? tags.slice(0, max) : tags;
  const extra = tags.length - shown.length;
  return (
    <div className={cn("flex flex-wrap items-center gap-1", className)}>
      {shown.map((tag) => (
        <TagChip key={tag.name} tag={tag} />
      ))}
      {extra > 0 && <span className="text-xs text-muted-foreground">+{extra}</span>}
    </div>
  );
}
