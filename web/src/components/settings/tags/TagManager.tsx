/**
 * Tag Manager (Settings -> Tags): searchable, sortable, virtualized list of
 * every tag with per-entity usage counts, multi-select bulk merge / delete /
 * recolor, per-row edit / merge / delete, and a usage side sheet.
 * Destructive actions confirm with the affected counts (no type-to-confirm).
 */
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDownIcon, ArrowUpIcon, PencilIcon, SearchIcon, TagIcon, Trash2Icon } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import {
  useBulkDeleteTags,
  useBulkMergeTags,
  useBulkUpdateTags,
  useDeleteTag,
  useMergeTag,
  useTags,
} from "@/api/tags";
import type { TagOut } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EditTagDialog } from "@/components/settings/tags/EditTagDialog";
import { TagBulkBar } from "@/components/settings/tags/TagBulkBar";
import { TagUsageSheet } from "@/components/settings/tags/TagUsageSheet";
import { COUNT_COLUMNS, filterAndSort, plural, totalOf, type SortDir, type SortKey } from "@/components/settings/tags/tagSort";
import { TagChip } from "@/components/tags/TagChip";
import { TagPicker } from "@/components/tags/TagPicker";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const ROW_H = 52;
// checkbox | tag | 7 count columns | actions
const GRID = "grid grid-cols-[2rem_minmax(12rem,1fr)_repeat(7,3.75rem)_7.5rem] items-center gap-2 px-3";
const MIN_W = "min-w-[52rem]";

const errMsg = (e: unknown, fallback: string) => (e instanceof ApiError ? e.detail : fallback);

export function TagManager() {
  const { t } = useTranslation();
  const tagsQuery = useTags();
  const all = useMemo(() => tagsQuery.data ?? [], [tagsQuery.data]);

  const [query, setQuery] = useState("");
  const [unusedOnly, setUnusedOnly] = useState(false);
  const [sort, setSort] = useState<SortKey>("name");
  const [dir, setDir] = useState<SortDir>("asc");
  const [selection, setSelection] = useState<Set<number>>(new Set());
  const lastClicked = useRef<number | null>(null);

  const [editing, setEditing] = useState<TagOut | null>(null);
  const [usageOf, setUsageOf] = useState<TagOut | null>(null);
  const [deleting, setDeleting] = useState<TagOut | null>(null);
  const [merging, setMerging] = useState<{ source: TagOut; into: TagOut } | null>(null);
  const [bulkDelete, setBulkDelete] = useState(false);
  const [bulkMergeInto, setBulkMergeInto] = useState<TagOut | null>(null);

  const deleteTag = useDeleteTag();
  const mergeTag = useMergeTag();
  const bulkDeleteTags = useBulkDeleteTags();
  const bulkMergeTags = useBulkMergeTags();
  const bulkUpdateTags = useBulkUpdateTags();
  const bulkBusy = bulkDeleteTags.isPending || bulkMergeTags.isPending || bulkUpdateTags.isPending;

  const visible = useMemo(() => filterAndSort(all, { query, unusedOnly, sort, dir }), [all, query, unusedOnly, sort, dir]);
  const byId = useMemo(() => new Map(all.map((tag) => [tag.id, tag])), [all]);
  // Selection only ever refers to tags that still exist.
  const selected = useMemo(() => [...selection].filter((id) => byId.has(id)).map((id) => byId.get(id)!), [selection, byId]);
  const selectedUses = selected.reduce((n, tag) => n + totalOf(tag), 0);

  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: visible.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_H,
    overscan: 8,
  });

  function toggleSort(key: SortKey) {
    if (key === sort) setDir(dir === "asc" ? "desc" : "asc");
    else {
      setSort(key);
      setDir(key === "name" ? "asc" : "desc");
    }
  }

  function clickRow(index: number, shift: boolean) {
    const id = visible[index].id;
    setSelection((prev) => {
      const next = new Set(prev);
      const anchor = lastClicked.current === null ? -1 : visible.findIndex((tag) => tag.id === lastClicked.current);
      if (shift && anchor >= 0) {
        const [lo, hi] = anchor < index ? [anchor, index] : [index, anchor];
        const on = !prev.has(id);
        for (let i = lo; i <= hi; i++) {
          if (on) next.add(visible[i].id);
          else next.delete(visible[i].id);
        }
      } else if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    lastClicked.current = id;
  }

  const allVisibleSelected = visible.length > 0 && visible.every((tag) => selection.has(tag.id));
  const someVisibleSelected = visible.some((tag) => selection.has(tag.id));

  function toggleAllVisible() {
    setSelection((prev) => {
      const next = new Set(prev);
      for (const tag of visible) {
        if (allVisibleSelected) next.delete(tag.id);
        else next.add(tag.id);
      }
      return next;
    });
  }

  function drop(ids: number[]) {
    setSelection((prev) => {
      const next = new Set(prev);
      for (const id of ids) next.delete(id);
      return next;
    });
  }

  // Bulk merge: the target may itself be selected; it is then not a source.
  const mergeSources = bulkMergeInto ? selected.filter((tag) => tag.id !== bulkMergeInto.id) : [];
  const mergeUses = mergeSources.reduce((n, tag) => n + totalOf(tag), 0);

  function runBulkMerge() {
    if (!bulkMergeInto || mergeSources.length === 0) return;
    const into = bulkMergeInto;
    const ids = mergeSources.map((tag) => tag.id);
    bulkMergeTags.mutate(
      { ids, intoId: into.id },
      {
        onSuccess: (res) => {
          drop(ids);
          toast.success(plural(t, "settings.tags.bulk.merged", res.results.length, { into: into.name }));
        },
        onError: (e) => toast.error(errMsg(e, t("settings.tags.bulk.error"))),
      },
    );
  }

  function runBulkDelete() {
    const ids = selected.map((tag) => tag.id);
    bulkDeleteTags.mutate(ids, {
      onSuccess: (res) => {
        drop(ids);
        toast.success(plural(t, "settings.tags.bulk.deleted", res.results.length));
      },
      onError: (e) => toast.error(errMsg(e, t("settings.tags.bulk.error"))),
    });
  }

  function runRecolor(hex: string) {
    bulkUpdateTags.mutate(
      { ids: selected.map((tag) => tag.id), color: hex },
      {
        onSuccess: (res) => toast.success(plural(t, "settings.tags.bulk.recolored", res.results.length)),
        onError: (e) => toast.error(errMsg(e, t("settings.tags.bulk.error"))),
      },
    );
  }

  function header(key: SortKey, label: string, align = "text-right") {
    const active = sort === key;
    return (
      <button
        type="button"
        onClick={() => toggleSort(key)}
        aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}
        className={cn(
          "inline-flex items-center gap-0.5 rounded px-1 text-xs font-medium uppercase tracking-wide text-muted-foreground hover:text-foreground",
          align === "text-right" && "justify-end",
          active && "text-foreground",
        )}
      >
        {label}
        {active && (dir === "asc" ? <ArrowUpIcon className="size-3" /> : <ArrowDownIcon className="size-3" />)}
      </button>
    );
  }

  let body;
  if (tagsQuery.isLoading) body = <Skeleton className="h-24 w-full rounded-lg" />;
  else if (tagsQuery.isError)
    body = (
      <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed py-8 text-center">
        <p role="alert" className="text-sm text-muted-foreground">
          {errMsg(tagsQuery.error, t("settings.tags.loadError"))}
        </p>
        <Button type="button" size="sm" onClick={() => void tagsQuery.refetch()}>
          {t("settings.tags.retry")}
        </Button>
      </div>
    );
  else if (all.length === 0) body = <EmptyState icon={TagIcon} title={t("settings.tags.empty")} />;
  else
    body = (
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative min-w-48 flex-1">
            <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              type="search"
              aria-label={t("settings.tags.search")}
              placeholder={t("settings.tags.searchPlaceholder")}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="pl-8"
            />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={unusedOnly} onCheckedChange={(v) => setUnusedOnly(v === true)} />
            {t("settings.tags.unusedOnly")}
          </label>
          <span className="text-sm text-muted-foreground" data-testid="tag-count">
            {t("settings.tags.count", { shown: visible.length, total: all.length })}
          </span>
        </div>

        {selected.length > 0 && (
          <TagBulkBar
            count={selected.length}
            all={all}
            busy={bulkBusy}
            onPickMergeTarget={setBulkMergeInto}
            onDelete={() => setBulkDelete(true)}
            onRecolor={runRecolor}
            onClear={() => setSelection(new Set())}
          />
        )}

        <div ref={scrollRef} className="h-[60vh] min-h-80 overflow-auto rounded-lg border" data-testid="tag-scroll">
          <div className={MIN_W}>
            <div className={cn(GRID, "sticky top-0 z-10 h-10 border-b bg-card")} role="row">
              <Checkbox
                aria-label={t("settings.tags.selectAll")}
                checked={allVisibleSelected ? true : someVisibleSelected ? "indeterminate" : false}
                onCheckedChange={toggleAllVisible}
                disabled={visible.length === 0}
              />
              {header("name", t("settings.tags.col.tag"), "text-left")}
              {COUNT_COLUMNS.map((key) => (
                <div key={key} className="flex justify-end">
                  {header(key, t(`settings.tags.col.${key}`))}
                </div>
              ))}
              <span className="text-right text-xs font-medium uppercase tracking-wide text-muted-foreground">
                {t("settings.tags.col.actions")}
              </span>
            </div>
            {visible.length === 0 ? (
              <EmptyState icon={SearchIcon} title={t("settings.tags.noMatch")} />
            ) : (
              <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
                {virtualizer.getVirtualItems().map((vi) => {
                  const tag = visible[vi.index];
                  const isSel = selection.has(tag.id);
                  return (
                    <div
                      key={tag.id}
                      role="row"
                      data-testid="tag-row"
                      data-selected={isSel || undefined}
                      className={cn(GRID, "absolute inset-x-0 border-b hover:bg-muted/40", isSel && "bg-muted/60")}
                      style={{ height: ROW_H, transform: `translateY(${vi.start}px)` }}
                    >
                      <Checkbox
                        aria-label={t("settings.tags.selectRow", { name: tag.name })}
                        checked={isSel}
                        onCheckedChange={() => {}}
                        onClick={(e) => clickRow(vi.index, e.shiftKey)}
                      />
                      <button
                        type="button"
                        aria-label={t("settings.tags.viewUsage", { name: tag.name })}
                        onClick={() => setUsageOf(tag)}
                        className="flex min-w-0 flex-col items-start gap-0.5 text-left"
                      >
                        <TagChip tag={tag} />
                        {tag.description && (
                          <span className="max-w-full truncate text-xs text-muted-foreground">{tag.description}</span>
                        )}
                      </button>
                      {COUNT_COLUMNS.map((key) => {
                        const n = tag.counts?.[key] ?? 0;
                        return (
                          <span
                            key={key}
                            className={cn("text-right text-sm tabular-nums", n === 0 && "text-muted-foreground/50")}
                          >
                            {n}
                          </span>
                        );
                      })}
                      <div className="flex justify-end gap-0.5">
                        <Button
                          type="button"
                          size="icon-sm"
                          variant="ghost"
                          aria-label={`${t("settings.tags.edit")} ${tag.name}`}
                          onClick={() => setEditing(tag)}
                        >
                          <PencilIcon className="size-3.5" />
                        </Button>
                        <TagPicker
                          label={t("settings.tags.mergeInto")}
                          options={all.filter((o) => o.id !== tag.id)}
                          onPick={(into) => setMerging({ source: tag, into: byId.get(into.id) ?? into })}
                          trigger={
                            <Button type="button" size="sm" variant="ghost" aria-label={`${t("settings.tags.mergeInto")} ${tag.name}`}>
                              {t("settings.tags.merge")}
                            </Button>
                          }
                        />
                        <Button
                          type="button"
                          size="icon-sm"
                          variant="ghost"
                          aria-label={`${t("settings.tags.delete")} ${tag.name}`}
                          onClick={() => setDeleting(tag)}
                        >
                          <Trash2Icon className="size-3.5 text-destructive" />
                        </Button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    );

  const delUses = deleting ? totalOf(deleting) : 0;
  const mergeOneUses = merging ? totalOf(merging.source) : 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settings.tags.title")}</CardTitle>
        <CardDescription>{t("settings.tags.description")}</CardDescription>
      </CardHeader>
      <CardContent>{body}</CardContent>

      <EditTagDialog tag={editing} onClose={() => setEditing(null)} />
      <TagUsageSheet tag={usageOf} onClose={() => setUsageOf(null)} />

      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title={t("settings.tags.deleteTitle", { name: deleting?.name ?? "" })}
        description={delUses > 0 ? plural(t, "settings.tags.deleteUsed", delUses) : t("settings.tags.deleteUnused")}
        confirmLabel={t("settings.tags.delete")}
        destructive
        onConfirm={() => {
          if (!deleting) return;
          const id = deleting.id;
          deleteTag.mutate(id, {
            onSuccess: () => drop([id]),
            onError: (e) => toast.error(errMsg(e, t("settings.tags.bulk.error"))),
          });
        }}
      />

      <ConfirmDialog
        open={merging !== null}
        onOpenChange={(open) => !open && setMerging(null)}
        title={t("settings.tags.mergeTitle", { name: merging?.source.name ?? "", into: merging?.into.name ?? "" })}
        description={
          mergeOneUses > 0
            ? plural(t, "settings.tags.mergeUsed", mergeOneUses, { name: merging?.source.name, into: merging?.into.name })
            : t("settings.tags.mergeUnused", { name: merging?.source.name ?? "" })
        }
        confirmLabel={t("settings.tags.merge")}
        destructive
        onConfirm={() => {
          if (!merging) return;
          const { source, into } = merging;
          mergeTag.mutate(
            { id: source.id, intoId: into.id },
            {
              onSuccess: () => drop([source.id]),
              onError: (e) => toast.error(errMsg(e, t("settings.tags.bulk.error"))),
            },
          );
        }}
      />

      <ConfirmDialog
        open={bulkMergeInto !== null}
        onOpenChange={(open) => !open && setBulkMergeInto(null)}
        title={
          mergeSources.length > 0
            ? plural(t, "settings.tags.bulk.mergeTitle", mergeSources.length, { into: bulkMergeInto?.name ?? "" })
            : t("settings.tags.bulk.mergeInto")
        }
        description={plural(t, "settings.tags.bulk.mergeDesc", mergeUses, { into: bulkMergeInto?.name ?? "" })}
        confirmLabel={t("settings.tags.merge")}
        confirmDisabled={mergeSources.length === 0}
        destructive
        onConfirm={runBulkMerge}
      />

      <ConfirmDialog
        open={bulkDelete}
        onOpenChange={setBulkDelete}
        title={plural(t, "settings.tags.bulk.deleteTitle", selected.length)}
        description={plural(t, "settings.tags.bulk.deleteDesc", selectedUses)}
        confirmLabel={t("settings.tags.delete")}
        destructive
        onConfirm={runBulkDelete}
      />
    </Card>
  );
}
