import { useSetEntityTags } from "@/api/tags";
import { TagChip } from "@/components/tags/TagChip";
import { TagPicker } from "@/components/tags/TagPicker";
import type { ModelDetail } from "@/api/types";

/** A model's tag chips; in edit mode each chip is removable and the picker
 * adds/creates tags (entering edit mode is itself the consent step, so
 * individual removes stay unconfirmed). */
export function TagEditor({ model, editMode }: { model: ModelDetail; editMode: boolean }) {
  const setTags = useSetEntityTags();
  const refs = model.tag_refs ?? [];
  const target = { kind: "model", id: model.id } as const;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {refs.map((tag) => (
        <TagChip
          key={tag.id}
          tag={tag}
          onRemove={editMode ? () => setTags.mutate({ target, refs: refs.filter((t) => t.id !== tag.id) }) : undefined}
        />
      ))}
      {refs.length === 0 && !editMode && <span className="text-xs text-muted-foreground">No tags</span>}
      {editMode && <TagPicker label="Tags" value={refs} onChange={(next) => setTags.mutate({ target, refs: next })} />}
    </div>
  );
}
