import type { TagRef } from "@/api/types";

/** `tag_refs` when the payload has them, else name-only refs from legacy `tags`. */
export function tagRefsOf(entity: { tag_refs?: TagRef[]; tags?: string[] }): TagRef[] {
  if (entity.tag_refs) return entity.tag_refs;
  return (entity.tags ?? []).map((name, index) => ({ id: -(index + 1), name }));
}
