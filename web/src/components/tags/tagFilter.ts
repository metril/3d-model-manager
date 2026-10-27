import type { TagMode } from "@/api/tags";
import type { TagRef } from "@/api/types";

export interface TagFilterValue {
  tags: number[];
  mode: TagMode;
}

export const EMPTY_TAG_FILTER: TagFilterValue = { tags: [], mode: "any" };

/** Client-side equivalent of the server's `tags` + `tag_mode` filter, for
 * lists that already hold `tag_refs` (a model's files, attachments). */
export function matchesTagFilter(refs: TagRef[] | undefined, filter: TagFilterValue): boolean {
  if (filter.tags.length === 0) return true;
  const have = new Set((refs ?? []).map((tag) => tag.id));
  return filter.mode === "all" ? filter.tags.every((id) => have.has(id)) : filter.tags.some((id) => have.has(id));
}
