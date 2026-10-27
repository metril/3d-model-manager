import { useState, type ReactNode } from "react";
import { CheckIcon, PlusIcon, TagIcon } from "lucide-react";

import { useCreateTag, useTags } from "@/api/tags";
import { TagChip } from "@/components/tags/TagChip";
import { TagForm } from "@/components/tags/TagForm";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import type { TagRef } from "@/api/types";

interface BaseProps {
  /** Custom trigger; defaults to a small "+" icon button. */
  trigger?: ReactNode;
  align?: "start" | "center" | "end";
  disabled?: boolean;
  /** Restrict the list to these tags and disable inline creation. */
  options?: TagRef[];
  /** Label for the default trigger / popover. */
  label?: string;
}

interface MultiProps extends BaseProps {
  /** The entity's current tag set; each toggle reports the full new set. */
  value: TagRef[];
  onChange: (next: TagRef[]) => void;
  onPick?: undefined;
}

interface PickProps extends BaseProps {
  /** Single-pick mode (bulk actions): picking a tag reports it and closes. */
  onPick: (tag: TagRef) => void;
  value?: undefined;
  onChange?: undefined;
}

/** Popover to search, toggle and inline-create tags. Creating a tag opens a
 * full form (color swatches/hex, icon grid, description) before it is saved. */
export function TagPicker(props: MultiProps | PickProps) {
  const { trigger, align = "start", disabled, options, label = "Tags" } = props;
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const tagsQuery = useTags();
  const createTag = useCreateTag();

  const all: TagRef[] = options ?? tagsQuery.data ?? [];
  const selectedIds = new Set((props.value ?? []).map((tag) => tag.id));
  const needle = search.trim().toLowerCase();
  const matches = all.filter((tag) => tag.name.toLowerCase().includes(needle));
  const exact = all.some((tag) => tag.name.toLowerCase() === needle);
  const canCreate = !options && needle.length > 0 && !exact;

  function close(next: boolean) {
    setOpen(next);
    if (!next) {
      setSearch("");
      setCreating(false);
    }
  }

  function choose(tag: TagRef) {
    if (props.onPick) {
      props.onPick(tag);
      close(false);
      return;
    }
    props.onChange(selectedIds.has(tag.id) ? props.value.filter((t) => t.id !== tag.id) : [...props.value, tag]);
  }

  return (
    <Popover open={open} onOpenChange={close}>
      <PopoverTrigger asChild>
        {trigger ?? (
          <Button type="button" variant="outline" size="icon-sm" aria-label={`Edit ${label.toLowerCase()}`} disabled={disabled}>
            <PlusIcon className="size-3.5" />
          </Button>
        )}
      </PopoverTrigger>
      <PopoverContent align={align} className="w-72 p-2">
        {creating ? (
          <TagForm
            initial={{ name: search.trim() }}
            submitLabel="Create tag"
            pending={createTag.isPending}
            onCancel={() => setCreating(false)}
            onSubmit={(values) =>
              createTag.mutate(values, {
                onSuccess: (created) => {
                  setCreating(false);
                  setSearch("");
                  choose(created);
                },
              })
            }
          />
        ) : (
          <div className="space-y-1.5">
            <Input
              autoFocus
              aria-label="Search tags"
              placeholder={canCreate || !options ? "Search or create…" : "Search…"}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  if (canCreate) setCreating(true);
                  else if (matches.length === 1) choose(matches[0]!);
                }
              }}
            />
            <ul className="max-h-52 space-y-0.5 overflow-y-auto" aria-label="Tags">
              {matches.map((tag) => {
                const selected = selectedIds.has(tag.id);
                return (
                  <li key={tag.id}>
                    <button
                      type="button"
                      aria-pressed={props.onPick ? undefined : selected}
                      onClick={() => choose(tag)}
                      className={cn(
                        "flex w-full items-center gap-2 rounded px-2 py-1 text-left hover:bg-muted",
                        selected && "bg-muted/60",
                      )}
                    >
                      <TagChip tag={tag} />
                      <CheckIcon className={cn("ml-auto size-3.5 shrink-0", selected ? "opacity-100" : "opacity-0")} />
                    </button>
                  </li>
                );
              })}
              {matches.length === 0 && !canCreate && (
                <li className="px-2 py-1 text-xs text-muted-foreground">{options ? "No tags" : "No tags yet"}</li>
              )}
            </ul>
            {canCreate && (
              <Button type="button" variant="ghost" size="sm" className="w-full justify-start" onClick={() => setCreating(true)}>
                <TagIcon /> Create “{search.trim()}”
              </Button>
            )}
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
