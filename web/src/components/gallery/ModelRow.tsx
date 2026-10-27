import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { FileStackIcon, StarIcon } from "lucide-react";

import { modelQueryOptions, usePatchModel, useTagColorMap } from "@/api/library";
import { OpenInSlicerButton } from "@/components/model-detail/OpenInSlicerButton";
import { SendToPrinterButton } from "@/components/model-detail/SendToPrinterButton";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { formatIcon } from "@/lib/formatMeta";
import { formatDate } from "@/lib/format";
import { tagColorClass } from "@/lib/tagColors";
import { LIST_ROW_HEIGHT_PX } from "@/lib/grid";
import { cn } from "@/lib/utils";
import type { ModelSummary } from "@/api/types";

const VISIBLE_TAGS = 2;

/** List-view row (R13b): a horizontal, fixed-height (`LIST_ROW_HEIGHT_PX`)
 * alternative to `ModelCard`'s grid tile -- same selection/quick-action
 * affordances, laid out as a single line instead of a card. Meant to be
 * virtualized one-per-row (unlike the grid's `chunkIntoRows` fan-out), so its
 * own height must always match `LIST_ROW_HEIGHT_PX` exactly. */
export function ModelRow({
  model,
  index,
  selected = false,
  onSelectChange,
  onModifiedClick,
}: {
  model: ModelSummary;
  /** Position in the gallery's flat item list -- ctrl/cmd/shift+click range
   * selection, same contract as `ModelCard`. */
  index?: number;
  selected?: boolean;
  onSelectChange?: (id: number, next: boolean) => void;
  onModifiedClick?: (event: React.MouseEvent, index: number) => void;
}) {
  const [coverErrored, setCoverErrored] = useState(false);
  const patchModel = usePatchModel(model.slug);
  const queryClient = useQueryClient();
  const tagColors = useTagColorMap();

  function onIntent() {
    void queryClient.prefetchQuery({ ...modelQueryOptions(model.slug), staleTime: 30_000 });
  }

  const visibleTags = model.tags.slice(0, VISIBLE_TAGS);
  const overflowCount = model.tags.length - visibleTags.length;
  const uniqueFormats = Array.from(new Set(model.formats));
  const primaryFormat = model.formats[0];
  const Icon = formatIcon(primaryFormat);
  const showCover = model.cover !== null && !coverErrored;
  const hasQuickActions = model.best_slicer_file !== null || model.printable_file !== null;

  function stopRowNavigation(event: React.MouseEvent) {
    event.preventDefault();
    event.stopPropagation();
  }

  function onLinkClick(event: React.MouseEvent) {
    if (onModifiedClick && (event.shiftKey || event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      onModifiedClick(event, index ?? 0);
    }
  }

  return (
    <Link
      to="/models/$slug"
      params={{ slug: model.slug }}
      className="group flex items-center gap-3 rounded-lg border border-transparent px-2 hover:border-border hover:bg-muted/50"
      style={{ height: LIST_ROW_HEIGHT_PX }}
      preload="intent"
      onClick={onLinkClick}
      onPointerEnter={onIntent}
      onFocus={onIntent}
    >
      <span className="contents" onClick={stopRowNavigation}>
        <Checkbox
          checked={selected}
          onCheckedChange={(checked) => onSelectChange?.(model.id, checked === true)}
          aria-label={`Select ${model.name}`}
          className={cn(
            "shrink-0 transition-opacity",
            selected ? "opacity-100" : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100",
          )}
        />
      </span>

      <div className="relative size-24 shrink-0 overflow-hidden rounded-md bg-muted">
        {showCover ? (
          <img
            src={model.cover ?? undefined}
            alt={model.name}
            loading="lazy"
            decoding="async"
            fetchPriority="low"
            className="size-full object-cover"
            onError={() => setCoverErrored(true)}
          />
        ) : (
          <div className="flex size-full items-center justify-center text-muted-foreground" data-testid="format-thumb">
            <Icon className="size-6" />
          </div>
        )}
      </div>

      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2">
          <h3 className="truncate text-sm font-medium" title={model.name}>
            {model.name}
          </h3>
          {model.category && (
            <Badge variant="outline" className="shrink-0 gap-1">
              <span
                aria-hidden="true"
                className={cn("size-1.5 rounded-full", tagColorClass(model.category.color) ?? "bg-muted-foreground")}
              />
              {model.category.name}
            </Badge>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {visibleTags.map((tag) => (
            <Badge key={tag} variant="secondary" className={tagColorClass(tagColors[tag])}>
              {tag}
            </Badge>
          ))}
          {overflowCount > 0 && <Badge variant="outline">+{overflowCount}</Badge>}
          {uniqueFormats.map((format) => (
            <Badge key={format} variant="outline">
              {format}
            </Badge>
          ))}
        </div>
      </div>

      <div className="hidden shrink-0 items-center gap-1 text-xs text-muted-foreground sm:flex" data-testid="model-row-meta">
        <FileStackIcon className="size-3" />
        {model.file_count} {model.file_count === 1 ? "file" : "files"}
      </div>

      <p className="hidden shrink-0 font-mono text-xs text-muted-foreground md:block">
        Updated {formatDate(model.updated_at)}
      </p>

      <span className="contents" onClick={stopRowNavigation}>
        <button
          type="button"
          aria-label={model.favorite ? "Remove from favorites" : "Add to favorites"}
          aria-pressed={model.favorite}
          onClick={() => patchModel.mutate({ favorite: !model.favorite })}
          className="shrink-0 rounded-full p-1 text-foreground transition-colors hover:text-warning"
        >
          <StarIcon className={model.favorite ? "size-4 fill-warning text-warning" : "size-4"} />
        </button>
      </span>

      {hasQuickActions && (
        <span className="contents" onClick={stopRowNavigation}>
          <div className="flex shrink-0 items-center gap-1">
            {model.best_slicer_file && <OpenInSlicerButton file={model.best_slicer_file} size="icon-sm" />}
            {(model.printable_file ?? model.best_slicer_file) && (
              <SendToPrinterButton file={(model.printable_file ?? model.best_slicer_file)!} />
            )}
          </div>
        </span>
      )}
    </Link>
  );
}
