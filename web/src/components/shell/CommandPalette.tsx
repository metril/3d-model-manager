import { useEffect, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Bookmark, ListChecks, ListOrdered, PrinterIcon, Shapes } from "lucide-react";

import { useAuth } from "@/api/auth";
import { useFeatures } from "@/api/features";
import { useCategories } from "@/api/categories";
import { useFollowedCollections } from "@/api/collections";
import { useModelSearchQuery } from "@/api/library";
import { usePrinters } from "@/api/printers";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";
import { ALL_NAV_ITEMS, filterNavItems, printerNavItems } from "@/components/shell/navItems";
import { PrintDialog } from "@/components/print/PrintDialog";
import { useHotkeys } from "@/hooks/useHotkeys";

const SEARCH_DEBOUNCE_MS = 200;

/** cmdk palette (R12): Mod+K anywhere opens it (registered here so any
 * mount point works -- `AppShell` just owns the open/closed boolean).
 * Three sections: static pages (`navItems.ts`), followed collections
 * (`useFollowedCollections`), and a debounced model search
 * (`useModelSearchQuery`, same hook the upload page's model picker uses). */
export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [printOpen, setPrintOpen] = useState(false);
  const { can } = useAuth();
  const printerEnabled = !!useFeatures().data?.printer_enabled;
  const followed = useFollowedCollections();
  const categoriesQuery = useCategories();
  const printersQuery = usePrinters({ enabled: printerEnabled });

  useHotkeys({ "mod+k": () => onOpenChange(!open) });

  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(query), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [query]);

  // Clear stale input/results once the dialog closes so reopening it starts
  // fresh instead of showing the last search.
  useEffect(() => {
    if (!open) {
      setQuery("");
      setDebounced("");
    }
  }, [open]);

  const trimmed = debounced.trim();
  const search = useModelSearchQuery(trimmed);
  const models = trimmed.length > 0 ? (search.data?.items ?? []) : [];
  const collections = followed.data ?? [];
  const categories = categoriesQuery.data ?? [];
  const printers = printerEnabled ? printerNavItems(printersQuery.data) : [];

  function goTo(to: string) {
    onOpenChange(false);
    void navigate({ to });
  }

  function goToPrinter(id: string) {
    onOpenChange(false);
    void navigate({ to: "/printers/$id", params: { id } });
  }

  function goToCollection(id: number) {
    onOpenChange(false);
    void navigate({ to: "/library", search: { collection: id } });
  }

  function goToCategory(id: number) {
    onOpenChange(false);
    void navigate({ to: "/library", search: { category: id } });
  }

  function goToModel(slug: string) {
    onOpenChange(false);
    void navigate({ to: "/models/$slug", params: { slug } });
  }

  return (
    <>
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <CommandInput placeholder="Search pages, collections, models…" value={query} onValueChange={setQuery} />
      <CommandList>
        <CommandEmpty>No results.</CommandEmpty>
        <CommandGroup heading="Pages">
          {filterNavItems(ALL_NAV_ITEMS, {
            printerEnabled,
            can,
            hasPrinter: (printersQuery.data?.length ?? 0) > 0,
          }).map((item) => (
            <CommandItem key={item.to as string} value={item.label} onSelect={() => goTo(item.to as string)}>
              <item.icon className="size-4" />
              {item.label}
            </CommandItem>
          ))}
        </CommandGroup>
        {printers.length > 0 && (
          <>
            <CommandSeparator />
            <CommandGroup heading="Printers">
              {printers.map((item) => (
                <CommandItem
                  key={`printer-${item.params?.id}`}
                  value={`printer-${item.label}`}
                  onSelect={() => goToPrinter(item.params?.id as string)}
                >
                  <item.icon className="size-4" />
                  {item.label}
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        )}
        {collections.length > 0 && (
          <>
            <CommandSeparator />
            <CommandGroup heading="Collections">
              {collections.map((c) => (
                <CommandItem
                  key={c.id}
                  value={`collection-${c.title}`}
                  onSelect={() => goToCollection(c.id)}
                >
                  <Bookmark className="size-4" />
                  {c.title}
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        )}
        {categories.length > 0 && (
          <>
            <CommandSeparator />
            <CommandGroup heading="Categories">
              {categories.map((category) => (
                <CommandItem
                  key={category.id}
                  value={`category-${category.name}`}
                  onSelect={() => goToCategory(category.id)}
                >
                  <Shapes className="size-4" />
                  {category.name}
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        )}
        {models.length > 0 && (
          <>
            <CommandSeparator />
            <CommandGroup heading="Models">
              {models.map((m) => (
                <CommandItem key={m.id} value={`model-${m.name}-${m.slug}`} onSelect={() => goToModel(m.slug)}>
                  <span className="flex size-6 shrink-0 items-center justify-center overflow-hidden rounded bg-muted">
                    {m.cover ? (
                      <img src={m.cover} alt="" className="size-full object-cover" />
                    ) : (
                      <span className="size-full bg-muted" />
                    )}
                  </span>
                  <span className="truncate">{m.name}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        )}
        {printerEnabled && (
          <>
            <CommandSeparator />
            <CommandGroup heading="Actions">
              {can("printers:print") ? (
                <CommandItem
                  value="Print…"
                  onSelect={() => {
                    onOpenChange(false);
                    setPrintOpen(true);
                  }}
                >
                  <PrinterIcon className="size-4" />
                  Print…
                </CommandItem>
              ) : null}
              <CommandItem value="Open print queue" onSelect={() => goTo("/print/queue")}>
                <ListOrdered className="size-4" />
                Open print queue
              </CommandItem>
              <CommandItem
                value="Run history stats"
                onSelect={() => {
                  onOpenChange(false);
                  void navigate({ to: "/print/history", search: { tab: "stats" } });
                }}
              >
                <ListChecks className="size-4" />
                Run history stats
              </CommandItem>
            </CommandGroup>
          </>
        )}
      </CommandList>
    </CommandDialog>
    <PrintDialog open={printOpen} onOpenChange={setPrintOpen} source={{ kind: "pick" }} />
    </>
  );
}
