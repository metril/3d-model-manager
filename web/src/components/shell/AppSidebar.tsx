import { useEffect, useRef } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  ChevronsLeftIcon,
  HelpCircle,
  LoaderCircleIcon,
  LogOut,
  Plus,
  TriangleAlertIcon,
} from "lucide-react";

import { useAuth, useLogout } from "@/api/auth";
import { useCategories } from "@/api/categories";
import { useFollowedCollections } from "@/api/collections";
import { useFailedImportsCount } from "@/api/imports";
import { useReviewCount } from "@/api/queue";
import { useScanRuns } from "@/api/scan";
import type { ScanRunOut } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { InstallAppButton } from "@/components/InstallAppButton";
import { ThemeToggle } from "@/components/ThemeToggle";
import { ServiceStatusDot } from "@/components/shell/ServiceStatusDot";
import { SidebarPrinterWidget } from "@/components/shell/SidebarPrinterWidget";
import { useNavLabel } from "@/components/shell/useNavLabel";
import { useSidebarCollapsed } from "@/components/shell/useSidebarCollapsed";
import { type NavItem } from "@/components/shell/navItems";
import { useVisibleNav } from "@/components/shell/useVisibleNav";
import { tagColorClass } from "@/lib/tagColors";
import { cn } from "@/lib/utils";
import type { LibrarySearch } from "@/pages/librarySearch";

const MAX_COLLECTION_LINKS = 5;

// R9-D item 7: scan progress chip. `useScanRuns` (`@/api/scan`) already
// fetches the latest run and gets invalidated live by `useEvents.tsx`'s
// `job.updated` handler on every `scan_library` transition (queued/running/
// done/failed/skipped) -- no polling needed here at all. The backend has no
// dedicated progress-percent event; `files_hashed`/`files_seen` off the same
// `ScanRunOut` row double as the N/M count (indeterminate until
// `files_seen` is nonzero).
type ScanChipState = { kind: "running"; run: ScanRunOut } | { kind: "failed" };

/** Running/failed chip state; a finished scan is announced as a toast (see
 * `useScanDoneToast`) instead of a lingering pill. */
function useScanChipState(): ScanChipState | null {
  const { data } = useScanRuns();
  const latest = data?.[0];
  if (!latest) return null;
  if (latest.state === "queued" || latest.state === "running") return { kind: "running", run: latest };
  if (latest.state === "failed") return { kind: "failed" };
  return null;
}

/** Toasts "Scan done" once when a run seen running/queued in this session
 * finishes (never for a run that was already done on page load). */
function useScanDoneToast() {
  const { data } = useScanRuns();
  const navigate = useNavigate();
  const latest = data?.[0];
  const watching = useRef<number | null>(null);
  useEffect(() => {
    if (!latest) return;
    if (latest.state === "queued" || latest.state === "running") {
      watching.current = latest.id;
    } else if (latest.state === "done" && watching.current === latest.id) {
      watching.current = null;
      toast.success("Scan done", {
        action: { label: "View jobs", onClick: () => void navigate({ to: "/system/jobs" }) },
      });
    }
  }, [latest, navigate]);
}

function ScanChip({ collapsed }: { collapsed: boolean }) {
  const navigate = useNavigate();
  const state = useScanChipState();
  useScanDoneToast();
  if (!state || collapsed) return null;

  function goToJobs() {
    void navigate({ to: "/system/jobs" });
  }

  if (state.kind === "failed") {
    return (
      <button
        type="button"
        onClick={goToJobs}
        className="mx-2 mb-2 flex items-center gap-1.5 rounded-lg bg-destructive/10 px-2.5 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/20"
      >
        <TriangleAlertIcon className="size-3.5" />
        Scan failed
      </button>
    );
  }

  const { run } = state;
  const label = run.files_seen > 0 ? `Scanning… ${run.files_hashed}/${run.files_seen}` : "Scanning…";

  return (
    <button
      type="button"
      onClick={goToJobs}
      className="mx-2 mb-2 flex items-center gap-1.5 rounded-lg bg-muted px-2.5 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"
    >
      <LoaderCircleIcon className="size-3.5 animate-spin" />
      {label}
    </button>
  );
}

function NavLink({
  item,
  collapsed,
  badge,
  badgeAriaLabel,
}: {
  item: NavItem;
  collapsed: boolean;
  badge?: number;
  /** Overrides the default "N failed import(s)" label -- e.g. the Queue
   * item's "N awaiting review" (P7 Task 9). */
  badgeAriaLabel?: string;
}) {
  const navLabel = useNavLabel();
  const linkClass = cn(
    "relative flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground",
    collapsed && "justify-center px-0",
  );
  const link = item.externalUrl ? (
    <a href={item.externalUrl} target="_blank" rel="noopener noreferrer" className={linkClass}>
      <item.icon className="size-4 shrink-0" />
      {!collapsed && (item.translate === false ? item.label : navLabel(item.label))}
    </a>
  ) : (
    <Link
      to={item.to}
      // `item.params` is a plain `Record<string, string>` (see `navItems.ts`);
      // `Link`'s own `params` prop type expects something shaped by the
      // specific route `to` resolves to, which a generically-typed `NavItem`
      // can't express statically.
      params={item.params as never}
      activeOptions={{ exact: !!item.exact }}
      className={linkClass}
      activeProps={{ className: "bg-sidebar-accent font-medium text-foreground before:absolute before:inset-y-1.5 before:-left-2 before:w-0.5 before:rounded-full before:bg-sidebar-primary" }}
    >
      <item.icon className="size-4 shrink-0" />
      {!collapsed && (item.translate === false ? item.label : navLabel(item.label))}
      {badge ? (
        <span
          className={cn(
            "inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-medium text-destructive-foreground tabular-mono",
            collapsed ? "absolute top-0.5 right-1 h-3.5 min-w-3.5 px-0.5 text-[9px]" : "ml-auto",
          )}
          aria-label={badgeAriaLabel ?? `${badge} failed import${badge === 1 ? "" : "s"}`}
        >
          {badge > 99 ? "99+" : badge}
        </span>
      ) : null}
    </Link>
  );

  if (!collapsed) return link;

  return (
    <Tooltip>
      <TooltipTrigger asChild>{link}</TooltipTrigger>
      <TooltipContent side="right">{(item.translate === false ? item.label : navLabel(item.label))}</TooltipContent>
    </Tooltip>
  );
}

/** Grouped sidebar nav (P3 Task 6): Print/Library/Inventory groups from
 * `navItems.ts` plus System pinned at the bottom, a dynamic Collections
 * sublist, per-printer links under Print, and a collapsible icon-rail
 * mode. Owns its own data (auth, features, printers, scan chip, followed
 * collections) so `AppShell` stays a thin composition root.
 *
 * Below `lg` the collapsible rail becomes an off-canvas drawer instead: at
 * 400px width a permanently-visible `w-14`/`w-56` column leaves too little
 * room for the studio to lay out without horizontal overflow, so `AppShell`
 * hides it by default and opens it via `mobileOpen` (the topbar's hamburger
 * button). It closes itself on a nav click, Esc, or backdrop click; none of
 * that applies at `lg+`, where it's always visible and `mobileOpen` is
 * ignored. */
export function AppSidebar({
  onOpenShortcuts,
  mobileOpen,
  onCloseMobile,
}: {
  onOpenShortcuts: () => void;
  mobileOpen: boolean;
  onCloseMobile: () => void;
}) {
  const { data: me, can } = useAuth();
  const logout = useLogout();
  const navigate = useNavigate();
  const failedImports = useFailedImportsCount();
  const followed = useFollowedCollections();
  const categories = useCategories();
  const canReview = can("queue:review");
  const reviewCount = useReviewCount({ enabled: canReview });
  const [collapsed, setCollapsed] = useSidebarCollapsed();
  const { t } = useTranslation();
  const navLabel = useNavLabel();
  const { groups: visibleGroups, systemItems: visibleSystemItems, externalItems } = useVisibleNav();

  useEffect(() => {
    if (!mobileOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onCloseMobile();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [mobileOpen, onCloseMobile]);

  const collections = (followed.data ?? []).slice(0, MAX_COLLECTION_LINKS);
  const hasMoreCollections = (followed.data?.length ?? 0) > MAX_COLLECTION_LINKS;

  function handleLogout() {
    logout.mutate(undefined, {
      onSuccess: () => void navigate({ to: "/login" }),
    });
  }

  return (
    <>
      {/* Backdrop: mobile-drawer mode only (`lg:hidden`) -- clicking it
          closes the drawer the same as Esc or a nav click. */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          aria-hidden
          onClick={onCloseMobile}
        />
      )}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex h-svh min-h-0 w-64 shrink-0 flex-col overflow-hidden border-r border-sidebar-border bg-sidebar transition-transform duration-150",
          "lg:sticky lg:top-0 lg:z-auto lg:w-auto lg:translate-x-0 lg:transition-[width]",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
          collapsed && "lg:w-14",
          !collapsed && "lg:w-56",
        )}
        onClick={(event) => {
          // Close the drawer on any nav click (an <a> inside) below `lg` --
          // harmless at `lg+`, where `mobileOpen` is never true.
          if ((event.target as HTMLElement).closest("a")) onCloseMobile();
        }}
      >
        <div
        className={cn(
          "flex items-center justify-between gap-1 px-4 py-4",
          collapsed && "justify-center px-2",
        )}
      >
        {!collapsed && <span className="truncate text-base font-semibold tracking-tight">3D Model Manager</span>}
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={collapsed ? t("shell.expandSidebar") : t("shell.collapseSidebar")}
          onClick={() => setCollapsed((prev) => !prev)}
        >
          <ChevronsLeftIcon className={cn("size-4 transition-transform", collapsed && "rotate-180")} />
        </Button>
      </div>
      {can("library:write") ? (
      <div className={cn("px-2 pb-2", collapsed && "px-1.5")}>
        <Button asChild className={cn("w-full gap-2", collapsed ? "justify-center px-0" : "justify-start")}>
          <Link to="/add" title={collapsed ? "Add to library" : undefined}>
            <Plus className="size-4" />
            {!collapsed && "Add to library"}
          </Link>
        </Button>
      </div>
      ) : null}
      <ScanChip collapsed={collapsed} />
      <nav className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-2 pb-2">
        {visibleGroups.map((group) => (
          <div key={group.label} className="flex flex-col gap-1">
            {!collapsed && (
              <div className="label-caps px-2.5 pb-1">
                {navLabel(group.label)}
              </div>
            )}
            {group.items.map((item) => (
              <div key={`${item.to as string}:${item.params?.id ?? ""}`}>
                <NavLink
                  item={item}
                  collapsed={collapsed}
                  badge={
                    item.to === "/collections"
                      ? failedImports
                      : item.to === "/print/queue"
                        ? reviewCount.data?.count
                        : undefined
                  }
                  badgeAriaLabel={
                    item.to === "/print/queue" && reviewCount.data?.count
                      ? `${reviewCount.data.count} awaiting review`
                      : undefined
                  }
                />
                {item.to === "/collections" && !collapsed && collections.length > 0 && (
                  <div className="mt-0.5 ml-5 flex flex-col gap-0.5 border-l border-border pl-2.5">
                    {collections.map((c) => (
                      <Link
                        key={c.id}
                        to="/library"
                        search={{ collection: c.id }}
                        className="truncate rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-sidebar-accent hover:text-foreground"
                        activeProps={{ className: "bg-sidebar-accent text-foreground" }}
                      >
                        {c.title}
                      </Link>
                    ))}
                    <Link
                      to="/collections"
                      className="truncate rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-sidebar-accent hover:text-foreground"
                    >
                      {hasMoreCollections ? `All ${followed.data?.length} collections` : "All collections"}
                    </Link>
                  </div>
                )}
              </div>
            ))}
          </div>
        ))}
        {/* Categories (R13b): NOT one of `navItems.ts`'s pages -- a
            dynamic, data-driven list like the Collections sublist above,
            just always visible (no parent nav item to nest under) rather
            than tied to one. Each link deep-links via `?category=<id>`,
            same one-way-seed contract `librarySearch.ts` already documents
            for `?collection=<id>`. */}
        {!collapsed && (categories.data?.length ?? 0) > 0 && (
          <div className="flex flex-col gap-1">
            <div className="label-caps px-2.5 pb-1">
              {t("nav.Categories")}
            </div>
            {(categories.data ?? []).map((category) => (
              <Link
                key={category.id}
                to="/library"
                search={(prev: LibrarySearch) => ({ ...prev, category: category.id })}
                className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
                activeProps={{ className: "bg-sidebar-accent font-medium text-foreground" }}
              >
                <span
                  aria-hidden="true"
                  className={cn("size-2 shrink-0 rounded-full", tagColorClass(category.color) ?? "bg-muted-foreground")}
                />
                <span className="truncate">{category.name}</span>
                <span className="ml-auto text-xs tabular-mono">{category.model_count}</span>
              </Link>
            ))}
          </div>
        )}
        {externalItems.length > 0 && (
          <div className="flex flex-col gap-1">
            {!collapsed && <div className="label-caps px-2.5 pb-1">{t("nav.Links")}</div>}
            {externalItems.map((item) => (
              <NavLink key={`ext:${item.params?.id}`} item={item} collapsed={collapsed} />
            ))}
          </div>
        )}
      </nav>
      <div className="flex shrink-0 flex-col gap-1 border-t border-sidebar-border px-2 py-2">
        {!collapsed && <div className="label-caps px-2.5 pb-1">System</div>}
        {visibleSystemItems.map((item) => (
          <NavLink key={item.to as string} item={item} collapsed={collapsed} />
        ))}
        <ServiceStatusDot collapsed={collapsed} />
        <SidebarPrinterWidget collapsed={collapsed} />
      </div>
      <div
        className={cn(
          "flex shrink-0 items-center justify-between gap-2 border-t border-sidebar-border px-4 py-3",
          collapsed && "flex-col gap-1.5 px-1.5",
        )}
      >
        {!collapsed && <span className="truncate text-sm text-muted-foreground">{me?.username}</span>}
        <div className={cn("flex items-center gap-1", collapsed && "flex-col")}>
          <InstallAppButton />
          <ThemeToggle />
          <Button
            type="button"
            variant="outline"
            size="icon"
            aria-label={t("shell.shortcuts")}
            onClick={onOpenShortcuts}
          >
            <HelpCircle className="size-4" />
          </Button>
          <Button
            type="button"
            variant="outline"
            size="icon"
            aria-label={t("shell.logout")}
            onClick={handleLogout}
            disabled={logout.isPending}
          >
            <LogOut className="size-4" />
          </Button>
        </div>
      </div>
      </aside>
    </>
  );
}
