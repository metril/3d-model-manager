/**
 * Single source of truth for the sidebar/topbar/command-palette nav (P3
 * Task 6, print-first shell). Three feature groups -- PRINT (gated behind
 * `useFeatures().data?.printer_enabled`), LIBRARY, INVENTORY -- plus
 * SYSTEM_ITEMS pinned at the bottom outside any group. `NAV_GROUPS`'
 * `featureGated: true` on the Print group is copied onto each of its
 * items when flattened into `ALL_NAV_ITEMS`, so a flat consumer (the
 * command palette) doesn't need to know groups exist at all. Finance (P7)
 * joins `SYSTEM_ITEMS` with `billingGated: true` -- `AppSidebar` resolves
 * that flag itself (via `useFinanceConfig()`), since it isn't a plain
 * permission check `filterNavItems`/`filterNavGroups` can express.
 */
import type { LinkProps } from "@tanstack/react-router";
import {
  BarChart3,
  Beaker,
  Boxes,
  Link2,
  Bookmark,
  CopyCheck,
  FolderKanban,
  LayoutDashboard,
  ListChecks,
  ListOrdered,
  Package,
  Plug,
  Plus,
  Printer,
  Settings,
  SquareLibrary,
  Trash2,
  Wallet,
  Wrench,
} from "lucide-react";
import type { ComponentType } from "react";

import type { ExternalLink } from "@/api/externalLinks";
import type { PrinterOut } from "@/api/types";
import type { Perm } from "@/lib/permissions";

export interface NavItem {
  to: LinkProps["to"];
  label: string;
  icon: ComponentType<{ className?: string }>;
  /** Gates the item behind `useFeatures().data?.printer_enabled`. */
  featureGated?: boolean;
  /** Hidden unless the user holds this permission. */
  perm?: Perm;
  /** Route params, e.g. a per-printer item's `{ id }` for `/printers/$id`. */
  params?: Record<string, string>;
  /** Exact-match activeOptions (the Print group's "/" Dashboard item, so it
   * doesn't read as active on every other route under it). */
  exact?: boolean;
  /** Hidden when there's no visible printer and the user can't add one
   * (`settings:write`) -- mirrors `redirectForIndex` (`routes.tsx`)'s rule
   * for `/`, so the nav never offers a link that would just redirect away
   * to `/library`. */
  requiresPrinterAccess?: boolean;
  /** Finance (P7): visible to a `billing:manage` holder always, and to
   * everyone else only once billing is on -- `AppSidebar` resolves this
   * with `useFinanceConfig()`, since it isn't a plain permission check. */
  billingGated?: boolean;
  /** Hidden unless the OrcaSlicer sidecar is enabled (`useOrcaSlicerEnabled`). */
  orcaslicerGated?: boolean;
  /** Set for admin-configured sidebar links with `open_mode: "newtab"`:
   * rendered as a plain `<a target="_blank">` instead of a router Link. */
  externalUrl?: string;
  /** `false` for user-provided labels (printer / external-link names): shown verbatim, never run through i18n. */
  translate?: false;
}

export interface NavGroup {
  label: "Print" | "Library" | "Inventory";
  items: NavItem[];
  /** The whole group disappears -- not just its items -- when the printer
   * feature is off. */
  featureGated?: boolean;
}

export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Print",
    featureGated: true,
    items: [
      { to: "/", label: "Dashboard", icon: LayoutDashboard, exact: true, requiresPrinterAccess: true },
      { to: "/print/queue", label: "Queue", icon: ListOrdered },
      { to: "/print/history", label: "History", icon: ListChecks },
      { to: "/print/plugs", label: "Plugs", icon: Plug },
      { to: "/stats", label: "Stats", icon: BarChart3 },
      { to: "/slicer", label: "OrcaSlicer", icon: Boxes, orcaslicerGated: true },
    ],
  },
  {
    label: "Library",
    items: [
      { to: "/library", label: "Models", icon: SquareLibrary },
      { to: "/collections", label: "Collections", icon: Bookmark },
      { to: "/projects", label: "Projects", icon: FolderKanban },
      { to: "/add", label: "Add", icon: Plus, perm: "library:write" },
      { to: "/duplicates", label: "Duplicates", icon: CopyCheck, perm: "library:write" },
      { to: "/trash", label: "Trash", icon: Trash2 },
    ],
  },
  {
    label: "Inventory",
    items: [
      { to: "/inventory/spools", label: "Spools", icon: Package },
      { to: "/inventory/materials", label: "Materials", icon: Beaker, perm: "inventory:write" },
      { to: "/inventory/profiles", label: "Profiles & recipes", icon: Wrench },
    ],
  },
];

/** Pinned below every group, not inside one -- Finance joins this list in
 * P7. */
export const SYSTEM_ITEMS: NavItem[] = [
  { to: "/system/jobs", label: "Background jobs", icon: ListChecks },
  { to: "/system/finance", label: "Finance", icon: Wallet, billingGated: true },
  { to: "/settings", label: "Settings", icon: Settings },
];

/** Flat list of every nav item (groups' `featureGated` copied onto their
 * items, SYSTEM_ITEMS appended) -- the palette's "Pages" section and the
 * topbar breadcrumb's path->label lookup both want this shape rather than
 * the grouped one. */
export const ALL_NAV_ITEMS: NavItem[] = [
  ...NAV_GROUPS.flatMap((group) =>
    group.items.map((item) => ({ ...item, featureGated: item.featureGated || group.featureGated })),
  ),
  ...SYSTEM_ITEMS,
];

/** Static path -> label lookup for the breadcrumb (dynamic routes like
 * `/models/$slug` or `/printers/$id` are handled separately in `TopBar`). */
export const NAV_LABELS: Record<string, string> = Object.fromEntries(
  ALL_NAV_ITEMS.map((item) => [item.to as string, item.label]),
);

/** Builds one nav item per configured printer, linking to its detail page
 * (`/printers/$id`). Always feature-gated -- there's nothing to link to
 * when the printer feature is off. */
export function printerNavItems(printers: PrinterOut[] | undefined): NavItem[] {
  return (printers ?? []).map((printer) => ({
    to: "/printers/$id",
    label: printer.name,
    icon: Printer,
    featureGated: true,
    translate: false,
    params: { id: String(printer.id) },
  }));
}

/** Drops items the printer feature flag or the user's permissions rule
 * out -- the command palette's flat "Pages" list. `hasPrinter` defaults to
 * `true` (no printer-visibility penalty) for callers that don't pass it. */
export function filterNavItems(
  items: NavItem[],
  opts: { printerEnabled: boolean; can: (p: Perm) => boolean; hasPrinter?: boolean; orcaslicerEnabled?: boolean },
): NavItem[] {
  const hasPrinter = opts.hasPrinter ?? true;
  return items.filter(
    (item) =>
      (!item.featureGated || opts.printerEnabled) &&
      (!item.orcaslicerGated || opts.orcaslicerEnabled) &&
      (!item.perm || opts.can(item.perm)) &&
      (!item.requiresPrinterAccess || hasPrinter || opts.can("settings:write")),
  );
}

/** Same filtering, kept at the group level: a feature-gated GROUP (Print)
 * disappears outright rather than leaving an empty heading, and any group
 * left with zero items after perm filtering (e.g. Inventory with no
 * `inventory:write`... still has Spools/Profiles, so this mostly matters
 * for future groups) is dropped too. */
export function filterNavGroups(
  groups: NavGroup[],
  opts: { printerEnabled: boolean; can: (p: Perm) => boolean; hasPrinter?: boolean; orcaslicerEnabled?: boolean },
): NavGroup[] {
  return groups
    .filter((group) => !group.featureGated || opts.printerEnabled)
    .map((group) => ({ ...group, items: filterNavItems(group.items, opts) }))
    .filter((group) => group.items.length > 0);
}

/** Admin-configured sidebar links (Q8a). Iframe links route to
 * `/external/$id`; new-tab links carry their URL in `externalUrl`. */
export function externalNavItems(links: ExternalLink[] | undefined): NavItem[] {
  return (links ?? []).map((link) => ({
    to: "/external/$id",
    label: link.name,
    icon: Link2,
    translate: false,
    params: { id: String(link.id) },
    externalUrl: link.open_mode === "newtab" ? link.url : undefined,
  }));
}
