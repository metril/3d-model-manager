import { useTranslation } from "react-i18next";
import { Link } from "@tanstack/react-router";

import { useRelatedModelsQuery } from "@/api/library";
import type { ModelDetail } from "@/api/types";

/** "More from <collection>" strip (collection provenance, Task 2) -- a
 * lightweight peek at other models pulled in from the same followed
 * collection as this one, so browsing an import doesn't dead-end. Renders
 * nothing for a manually-created model (no `source_collection_id`), and
 * nothing while loading or once the current model is excluded, so it never
 * flashes an empty heading. Deliberately a trimmed tile, not a reused
 * `ModelCard` -- this strip only needs a thumbnail + name, not the full
 * gallery card's spec row, tags, and review-state affordances. */
export function RelatedModels({ model }: { model: ModelDetail }) {
  const { t } = useTranslation();
  const collectionId = model.source_collection_id ?? undefined;
  const relatedQuery = useRelatedModelsQuery(collectionId);

  if (collectionId === undefined) return null;

  const items = (relatedQuery.data?.items ?? []).filter((item) => item.id !== model.id).slice(0, 5);
  if (items.length === 0) return null;

  return (
    <div className="space-y-2" data-testid="related-models">
      <h2 className="text-sm font-semibold text-foreground">
        {t("mdRelated.moreFrom", { title: model.source_collection_title ?? t("mdRelated.thisCollection") })}
      </h2>
      <div className="flex gap-3 overflow-x-auto pb-1">
        {items.map((item) => (
          <Link
            key={item.id}
            to="/models/$slug"
            params={{ slug: item.slug }}
            className="group flex w-28 shrink-0 flex-col gap-1.5"
          >
            <div className="aspect-square overflow-hidden rounded-lg bg-muted">
              {item.cover ? (
                <img
                  src={item.cover}
                  alt={item.name}
                  className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.04] motion-reduce:transition-none motion-reduce:group-hover:scale-100"
                />
              ) : (
                <div className="flex h-full w-full items-center justify-center text-[10px] text-muted-foreground">
                  {t("mdRelated.noPreview")}
                </div>
              )}
            </div>
            <p className="truncate text-xs font-medium text-foreground" title={item.name}>
              {item.name}
            </p>
          </Link>
        ))}
      </div>
    </div>
  );
}
