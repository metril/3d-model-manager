import { useLocation, useParams, useRouter } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { ArrowLeftIcon, MenuIcon, SearchIcon } from "lucide-react";

import { usePrinters } from "@/api/printers";
import { Button } from "@/components/ui/button";
import { useNavLabel } from "@/components/shell/useNavLabel";
import { NAV_LABELS } from "@/components/shell/navItems";

/** Slim topbar above the page content (P3 Task 6): a back button (hidden
 * on the print-dashboard root), a breadcrumb built from the nav config
 * plus special cases for the model detail and per-printer detail routes,
 * and a search affordance that opens the command palette. Below `lg` it
 * also carries the hamburger that opens the off-canvas sidebar drawer
 * (see `AppSidebar`). */
export function TopBar({
  onOpenPalette,
  onOpenMobileNav,
}: {
  onOpenPalette: () => void;
  onOpenMobileNav: () => void;
}) {
  const { t } = useTranslation();
  const navLabel = useNavLabel();
  const router = useRouter();
  const { pathname } = useLocation();
  // `strict: false` merges params from whichever matched route has them --
  // only `/models/$slug` and `/printers/$id` do, so these are `undefined`
  // everywhere else.
  const { slug, id } = useParams({ strict: false }) as { slug?: string; id?: string };
  const printers = usePrinters({ enabled: !!id });
  const isHome = pathname === "/";
  const printerName = id ? printers.data?.find((p) => String(p.id) === id)?.name ?? id : undefined;
  const crumb = slug
    ? `Library / ${slug}`
    : printerName
      ? `Print / ${printerName}`
      : (NAV_LABELS[pathname] ? navLabel(NAV_LABELS[pathname]) : "");

  return (
    <div className="flex items-center gap-3 border-b border-border bg-background px-6 py-2.5">
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label={t("shell.openMenu")}
        className="lg:hidden"
        onClick={onOpenMobileNav}
      >
        <MenuIcon className="size-4" />
      </Button>
      {!isHome && (
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={t("shell.back")}
          onClick={() => router.history.back()}
        >
          <ArrowLeftIcon className="size-4" />
        </Button>
      )}
      <div className="min-w-0 flex-1 truncate text-sm text-muted-foreground">{crumb}</div>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="gap-2 text-muted-foreground"
        onClick={onOpenPalette}
      >
        <SearchIcon className="size-3.5" />
        {t("shell.search")}
        <kbd className="rounded border border-border bg-muted px-1 text-[10px] tabular-mono">⌘K</kbd>
      </Button>
    </div>
  );
}
