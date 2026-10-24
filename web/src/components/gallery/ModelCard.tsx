import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { ClockIcon, FileStackIcon, StarIcon, XIcon } from "lucide-react";

import { modelQueryOptions, usePatchModel, useTagColorMap } from "@/api/library";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { OpenInSlicerButton } from "@/components/model-detail/OpenInSlicerButton";
import { SendToPrinterButton } from "@/components/model-detail/SendToPrinterButton";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { SpecRow, type SpecItem } from "@/components/ui/spec-row";
import { FORMAT_LABELS, formatIcon } from "@/lib/formatMeta";
import { formatDate, humanizeDuration } from "@/lib/format";
import { tagColorClass } from "@/lib/tagColors";
import { cn } from "@/lib/utils";
import type { ModelSummary } from "@/api/types";

const VISIBLE_TAGS = 3;
const VISIBLE_FORMATS = 3;

/** `W × D × H mm`, one decimal each -- `null` when the model has no
 * current-revision bounding box yet. */
function formatDims(dims: number[] | null): string | null {
  if (!dims || dims.length < 3) return null;
  const [w, d, h] = dims;
  return `${w.toFixed(1)} × ${d.toFixed(1)} × ${h.toFixed(1)} mm`;
}

export function ModelCard({
  model,
  index,
  selected = false,
  onSelectChange,
  onModifiedClick,
}: {
  model: ModelSummary;
  /** This card's position in the gallery's flat item list -- used only for
   * ctrl/cmd/shift+click range selection; optional so the card still works
   * standalone (e.g. in tests) without select support. */
  index?: number;
  /** Selection is implicit (no separate select-mode toggle): the checkbox
   * always exists, shown on hover or once `selected`. */
  selected?: boolean;
  onSelectChange?: (id: number, next: boolean) => void;
  /** Ctrl/Cmd/Shift+click range/toggle select (R9-A item 6): fired instead
   * of navigating when the card's `<Link>` is clicked with a modifier held. */
  onModifiedClick?: (event: React.MouseEvent, index: number) => void;
}) {
  const [coverErrored, setCoverErrored] = useState(false);
  const [renderErrored, setRenderErrored] = useState(false);
  // R9-A item 1: the hover-render `<img>` only gets a `src` once the card's
  // actually been hovered -- until then it stays mounted (so the opacity
  // crossfade still works once it does) but src-less, so the browser never
  // fetches a render for a card the user hasn't shown any intent on.
  const [hovered, setHovered] = useState(false);
  const patchModel = usePatchModel(model.slug);
  const queryClient = useQueryClient();
  const tagColors = useTagColorMap();

  // R9-A item 4: warm the model detail query on hover/focus intent so the
  // click-through navigation renders instantly. A `staleTime` keeps it from
  // being refetched immediately on mount if the user does follow through.
  function onIntent() {
    setHovered(true);
    void queryClient.prefetchQuery({ ...modelQueryOptions(model.slug), staleTime: 30_000 });
  }
  const visibleTags = model.tags.slice(0, VISIBLE_TAGS);
  const overflowCount = model.tags.length - visibleTags.length;
  const uniqueFormats = Array.from(new Set(model.formats));
  const visibleFormats = uniqueFormats.slice(0, VISIBLE_FORMATS);
  const formatOverflowCount = uniqueFormats.length - visibleFormats.length;
  const primaryFormat = model.formats[0];
  const Icon = formatIcon(primaryFormat);
  const dimsLabel = formatDims(model.dims_mm);
  const hasQuickActions = model.best_slicer_file !== null || model.printable_file !== null;
  const showCover = model.cover !== null && !coverErrored;
  // Photo-first cards, render on hover (feat/import-fidelity T4): `cover`
  // (now photo-first per T2) stays the card's resting image; a distinct
  // `render_url` -- the revision's own assembly-thumbnail render -- crossfades
  // in on hover as a second, absolutely-positioned <img> (CSS opacity only,
  // no JS hover-state) so there's no layout shift. Only rendered at all when
  // it would actually show something different from the resting cover.
  const showRenderHover =
    showCover && model.render_url !== null && model.render_url !== model.cover && !renderErrored;
  const needsReview = model.review_state === "adopted";

  // Datasheet spec row: only the fields the gallery summary actually carries
  // (filament colors/material live in per-blob plate metadata, off the gallery
  // hot path — those chips appear on the model detail + viewer instead).
  const specItems: Array<SpecItem | null> = [
    model.print_time_s !== null
      ? { icon: <ClockIcon />, label: humanizeDuration(model.print_time_s) }
      : null,
    model.has_sliced ? { label: "Sliced" } : null,
    model.file_count > 0
      ? {
          icon: <FileStackIcon />,
          label: `${model.file_count} ${model.file_count === 1 ? "file" : "files"}`,
        }
      : null,
  ];

  // The card body is a `<Link>` (whole-card navigation); the review-dismiss
  // confirm dialog must not also trigger that navigation, so stop every
  // click inside it before it reaches the anchor's handler.
  function stopCardNavigation(event: React.MouseEvent) {
    event.preventDefault();
    event.stopPropagation();
  }

  // R9-A item 6: a modified click selects instead of navigating. Any
  // modified click auto-enters select mode via the parent's handler.
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
      className="group block"
      preload="intent"
      onClick={onLinkClick}
      onPointerEnter={onIntent}
      onFocus={onIntent}
    >
      <Card className="h-full gap-3 overflow-hidden py-0 pb-4 transition-shadow hover:shadow-md">
        <div className="relative aspect-[4/3] overflow-hidden bg-muted">
          {showCover ? (
            <>
              <img
                src={model.cover ?? undefined}
                alt={model.name}
                loading="lazy"
                decoding="async"
                fetchPriority="low"
                className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.04] motion-reduce:transition-none motion-reduce:group-hover:scale-100"
                onError={() => setCoverErrored(true)}
              />
              {showRenderHover && (
                <img
                  src={hovered ? (model.render_url ?? undefined) : undefined}
                  alt=""
                  aria-hidden="true"
                  data-testid="render-hover-img"
                  loading="lazy"
                  decoding="async"
                  fetchPriority="low"
                  className="absolute inset-0 h-full w-full object-cover opacity-0 transition-opacity duration-300 group-hover:opacity-100 motion-reduce:transition-none"
                  onError={() => setRenderErrored(true)}
                />
              )}
            </>
          ) : (
            <div
              className="flex h-full w-full flex-col items-center justify-center gap-2 text-muted-foreground"
              data-testid="format-thumb"
            >
              <Icon className="size-10" />
              <span className="text-xs font-semibold tracking-wide">
                {primaryFormat ? FORMAT_LABELS[primaryFormat] : "—"}
              </span>
            </div>
          )}
          <div className="absolute top-2 left-2 flex flex-col items-start gap-1.5">
            {/* Selection is implicit (no select-mode toggle): the checkbox
                is always mounted, just hidden until hover/focus or until
                the card is actually selected. Same `display: contents` +
                stop-propagation trick as the review-dismiss control below --
                the card body is a whole-surface `<Link>`, and Radix's
                checkbox click would otherwise bubble up and navigate away
                instead of toggling selection. */}
            <span className="contents" onClick={stopCardNavigation}>
              <Checkbox
                checked={selected}
                onCheckedChange={(checked) => onSelectChange?.(model.id, checked === true)}
                aria-label={`Select ${model.name}`}
                className={cn(
                  "bg-background/80 backdrop-blur-sm transition-opacity",
                  selected ? "opacity-100" : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100",
                )}
              />
            </span>
            {model.source_site && (
              <Badge variant="secondary" className="capitalize backdrop-blur-sm" data-testid="source-badge">
                {model.source_site}
              </Badge>
            )}
          </div>
          <div className="absolute top-2 right-2 flex flex-col items-end gap-1.5">
            {needsReview && (
              <Badge variant="secondary" className="gap-1 pr-1 backdrop-blur-sm" data-testid="review-badge">
                Needs review
                {/* `display: contents` keeps this out of the Badge's flex
                    layout (so the trigger button still sizes/aligns exactly
                    as before) while still giving us a click handler that sees
                    every click inside -- including the dialog's confirm
                    button, which portals to `document.body` and would
                    otherwise bubble up through the *React* tree (portals
                    bubble via the component tree, not the DOM tree) into this
                    card's wrapping `<Link>` and navigate away. */}
                <span className="contents" onClick={stopCardNavigation}>
                  <ConfirmDialog
                    trigger={
                      <button
                        type="button"
                        aria-label="Dismiss needs review"
                        className="rounded-full hover:opacity-70"
                      >
                        <XIcon className="size-3" />
                      </button>
                    }
                    title='Clear "needs review"?'
                    confirmLabel="Clear"
                    onConfirm={() => patchModel.mutate({ review_state: null })}
                  />
                </span>
              </Badge>
            )}
            {/* A star is a deliberate, always-live action -- not gated
                behind edit mode like name/description/tags. Same
                stop-navigation wrapper as the dismiss control above; a
                favorited star stays visible even when the card isn't
                hovered (it's state, not a hover affordance). */}
            <span className="contents" onClick={stopCardNavigation}>
              <button
                type="button"
                aria-label={model.favorite ? "Remove from favorites" : "Add to favorites"}
                aria-pressed={model.favorite}
                onClick={() => patchModel.mutate({ favorite: !model.favorite })}
                className="rounded-full bg-background/80 p-1 text-foreground backdrop-blur-sm transition-colors hover:text-amber-500"
              >
                <StarIcon className={model.favorite ? "size-4 fill-amber-400 text-amber-500" : "size-4"} />
              </button>
            </span>
          </div>
          {(dimsLabel !== null || hasQuickActions) && (
            <div
              className="absolute inset-x-0 bottom-0 flex items-center justify-between gap-2 bg-gradient-to-t from-black/70 to-transparent px-2 py-1.5 opacity-0 transition-opacity duration-200 group-hover:opacity-100 group-focus-within:opacity-100"
              data-testid="hover-overlay"
            >
              {dimsLabel !== null ? (
                <span className="tabular-mono truncate text-xs text-white">{dimsLabel}</span>
              ) : (
                <span />
              )}
              {hasQuickActions && (
                <span className="contents" onClick={stopCardNavigation}>
                  <div className="flex items-center gap-1">
                    {model.best_slicer_file && (
                      <OpenInSlicerButton file={model.best_slicer_file} size="icon-sm" />
                    )}
                    {(model.printable_file ?? model.best_slicer_file) && (
              <SendToPrinterButton file={(model.printable_file ?? model.best_slicer_file)!} />
            )}
                  </div>
                </span>
              )}
            </div>
          )}
        </div>
        <CardContent className="flex flex-col gap-2 px-4">
          <div className="flex items-center gap-1.5">
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

          <SpecRow data-testid="model-spec" items={specItems} />

          {visibleTags.length > 0 && (
            <div className="flex min-h-5 flex-wrap gap-1">
              {visibleTags.map((tag) => (
                <Badge key={tag} variant="secondary" className={tagColorClass(tagColors[tag])}>
                  {tag}
                </Badge>
              ))}
              {overflowCount > 0 && <Badge variant="outline">+{overflowCount}</Badge>}
            </div>
          )}

          <div className="flex flex-wrap gap-1" data-testid="format-badges">
            {visibleFormats.map((format) => {
              const ChipIcon = formatIcon(format);
              return (
                <Badge key={format} variant="outline" className="gap-1">
                  <ChipIcon className="size-3" />
                  {FORMAT_LABELS[format]}
                </Badge>
              );
            })}
            {formatOverflowCount > 0 && <Badge variant="outline">+{formatOverflowCount}</Badge>}
          </div>

          <p className="font-mono text-xs text-muted-foreground">
            Updated {formatDate(model.updated_at)}
          </p>
        </CardContent>
      </Card>
    </Link>
  );
}
