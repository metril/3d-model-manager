import { lazy, Suspense, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
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
import { useOrcaSlicerEnabled } from "@/api/orcaslicer";
import { openOrcaSlicerWindow } from "@/components/orcaslicer/orcaslicerNav";
import { ALL_NAV_ITEMS, filterNavItems, printerNavItems } from "@/components/shell/navItems";
import { useNavLabel } from "@/components/shell/useNavLabel";

// The print wizard is heavy and only needed once "Print…" is picked.
const PrintDialog = lazy(() => import("@/components/print/PrintDialog").then((m) => ({ default: m.PrintDialog })));

const SEARCH_DEBOUNCE_MS = 200;

/** cmdk palette (R12): `AppShell` owns the open/closed boolean and the Mod+K
 * hotkey, and lazy-loads this component on first open.
 * Three sections: static pages (`navItems.ts`), followed collections
 * (`useFollowedCollections`), and a debounced model search
 * (`useModelSearchQuery`, same hook the upload page's model picker uses). */
export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const navLabel = useNavLabel();
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [printOpen, setPrintOpen] = useState(false);
  const { can } = useAuth();
  const printerEnabled = !!useFeatures().data?.printer_enabled;
  const orcaslicerEnabled = useOrcaSlicerEnabled();
  const followed = useFollowedCollections();
  const categoriesQuery = useCategories();
  const printersQuery = usePrinters({ enabled: printerEnabled });

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

  function openSlicer() {
    onOpenChange(false);
    openOrcaSlicerWindow();
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
      <CommandInput placeholder={t("cmdk.placeholder")} value={query} onValueChange={setQuery} />
      <CommandList>
        <CommandEmpty>{t("cmdk.empty")}</CommandEmpty>
        <CommandGroup heading={t("cmdk.pages")}>
          {filterNavItems(ALL_NAV_ITEMS, {
            printerEnabled,
            can,
            orcaslicerEnabled,
            hasPrinter: (printersQuery.data?.length ?? 0) > 0,
          }).map((item) => (
            <CommandItem key={item.to as string} value={navLabel(item.label)} onSelect={() => (item.orcaslicerWindow ? openSlicer() : goTo(item.to as string))}>
              <item.icon className="size-4" />
              {navLabel(item.label)}
            </CommandItem>
          ))}
        </CommandGroup>
        {printers.length > 0 && (
          <>
            <CommandSeparator />
            <CommandGroup heading={t("cmdk.printers")}>
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
            <CommandGroup heading={t("cmdk.collections")}>
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
            <CommandGroup heading={t("cmdk.categories")}>
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
            <CommandGroup heading={t("cmdk.models")}>
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
            <CommandGroup heading={t("cmdk.actions")}>
              {can("printers:print") ? (
                <CommandItem
                  value={t("cmdk.print")}
                  onSelect={() => {
                    onOpenChange(false);
                    setPrintOpen(true);
                  }}
                >
                  <PrinterIcon className="size-4" />
                  {t("cmdk.print")}
                </CommandItem>
              ) : null}
              <CommandItem value={t("cmdk.openQueue")} onSelect={() => goTo("/print/queue")}>
                <ListOrdered className="size-4" />
                {t("cmdk.openQueue")}
              </CommandItem>
              <CommandItem
                value={t("cmdk.historyStats")}
                onSelect={() => {
                  onOpenChange(false);
                  void navigate({ to: "/print/history", search: { tab: "stats" } });
                }}
              >
                <ListChecks className="size-4" />
                {t("cmdk.historyStats")}
              </CommandItem>
            </CommandGroup>
          </>
        )}
      </CommandList>
    </CommandDialog>
    {printOpen && (
      <Suspense fallback={null}>
        <PrintDialog open={printOpen} onOpenChange={setPrintOpen} source={{ kind: "pick" }} />
      </Suspense>
    )}
    </>
  );
}
