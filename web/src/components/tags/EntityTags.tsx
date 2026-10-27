import { useSetEntityTags, type TagTarget } from "@/api/tags";
import { TagChip } from "@/components/tags/TagChip";
import { TagPicker } from "@/components/tags/TagPicker";
import { cn } from "@/lib/utils";
import type { TagRef } from "@/api/types";

/** Chips + an add/edit picker for one taggable entity (file, project,
 * attachment, print archive, collection). Chips stay read-only when
 * `readOnly`; otherwise each is removable and the picker toggles/creates. */
export function EntityTags({
  target,
  tags,
  readOnly,
  className,
}: {
  target: TagTarget;
  tags: TagRef[] | undefined;
  readOnly?: boolean;
  className?: string;
}) {
  const setTags = useSetEntityTags();
  const refs = tags ?? [];
  return (
    <div className={cn("flex flex-wrap items-center gap-1", className)}>
      {refs.map((tag) => (
        <TagChip
          key={tag.id}
          tag={tag}
          onRemove={readOnly ? undefined : () => setTags.mutate({ target, refs: refs.filter((t) => t.id !== tag.id) })}
        />
      ))}
      {!readOnly && <TagPicker value={refs} onChange={(next) => setTags.mutate({ target, refs: next })} />}
    </div>
  );
}
