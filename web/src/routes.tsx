import type { QueryClient } from "@tanstack/react-query";
import { Outlet, createRootRouteWithContext, createRoute, redirect } from "@tanstack/react-router";

import type { Me } from "@/api/auth";
import { authQueryOptions } from "@/api/auth";
import { featuresQueryOptions } from "@/api/features";
import { printersQueryOptions } from "@/api/printers";
import type { Features, PrinterOut } from "@/api/types";
import { can } from "@/lib/permissions";
import { AppShell } from "@/components/AppShell";
import { AddPage } from "@/pages/AddPage";
import { CollectionsPage } from "@/pages/CollectionsPage";
import { DuplicatesPage } from "@/pages/DuplicatesPage";
import { JobsPage } from "@/pages/JobsPage";
import { LibraryPage } from "@/pages/LibraryPage";
import { LoginPage } from "@/pages/LoginPage";
import { MaterialsPage } from "@/pages/MaterialsPage";
import { ModelDetailPage } from "@/pages/ModelDetailPage";
import { PlaceholderPage } from "@/pages/PlaceholderPage";
import { PrintDashboardPage } from "@/pages/PrintDashboardPage";
import { PrinterDetailPage } from "@/pages/PrinterDetailPage";
import { PrintHistoryPage } from "@/pages/PrintHistoryPage";
import { QueuePage } from "@/pages/QueuePage";
import { SettingsPage } from "@/pages/SettingsPage";
import { SpoolsPage } from "@/pages/SpoolsPage";
import { ViewerWindowPage } from "@/pages/ViewerWindowPage";
import { parseCollectionsSearch } from "@/pages/collectionsSearch";
import { parseLibrarySearch, type LibrarySearch } from "@/pages/librarySearch";
import { parsePrintHistorySearch } from "@/pages/printHistorySearch";
import { parseViewerWindowSearch } from "@/pages/viewerWindowSearch";

export interface RouterContext {
  queryClient: QueryClient;
}

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: () => <Outlet />,
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  validateSearch: (search: Record<string, unknown>): { error?: string } => ({
    error: typeof search.error === "string" ? search.error : undefined,
  }),
  component: LoginPage,
});

/** Pathless layout route: guards every child on `GET /auth/me` before it
 * loads, redirecting to `/login` on failure, then renders the app shell
 * (sidebar nav + outlet) around whichever child matched. */
const authenticatedRoute = createRoute({
  id: "authenticated",
  getParentRoute: () => rootRoute,
  beforeLoad: async ({ context }) => {
    try {
      await context.queryClient.ensureQueryData(authQueryOptions);
    } catch {
      throw redirect({ to: "/login" });
    }
  },
  component: AppShell,
});

// Standalone viewer window (M8 G1): session-guarded but OUTSIDE the AppShell
// layout (no nav rail) so `window.open('/viewer/$slug?ids=&bg=&colors=')`
// renders a bare, self-contained canvas.
const viewerWindowRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/viewer/$slug",
  beforeLoad: async ({ context }) => {
    try {
      await context.queryClient.ensureQueryData(authQueryOptions);
    } catch {
      throw redirect({ to: "/login" });
    }
  },
  validateSearch: parseViewerWindowSearch,
  component: ViewerWindowPage,
});

/** Pure redirect policy for `/` (P3 Task 5): print-first by default, but
 * bounced to `/library` when there's nothing print-related to show --
 * the printer feature is off, or the caller can't see any printer and
 * can't add one either. Exported for unit coverage without driving a
 * router; `library search` keys on `/` (e.g. `?collection=`) are carried
 * straight through so an old bookmark still lands on the right filter. */
export function redirectForIndex(ctx: {
  features: Features | undefined;
  printers: PrinterOut[] | undefined;
  me: Me | undefined;
  search: LibrarySearch;
}): { to: "/library"; search: LibrarySearch } | null {
  const printerEnabled = !!ctx.features?.printer_enabled;
  const hasPrinter = (ctx.printers?.length ?? 0) > 0;
  const canAddPrinter = can(ctx.me, "settings:write");
  if (!printerEnabled || (!hasPrinter && !canAddPrinter)) {
    return { to: "/library", search: ctx.search };
  }
  return null;
}

const indexRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/",
  validateSearch: parseLibrarySearch,
  beforeLoad: async ({ context, search }) => {
    const features = await context.queryClient.ensureQueryData(featuresQueryOptions);
    const me = await context.queryClient.ensureQueryData(authQueryOptions);
    const printers = features.printer_enabled
      ? await context.queryClient.ensureQueryData(printersQueryOptions)
      : [];
    const target = redirectForIndex({ features, printers, me, search });
    if (target) throw redirect(target);
  },
  component: PrintDashboardPage,
});

const libraryRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/library",
  validateSearch: parseLibrarySearch,
  component: LibraryPage,
});

const modelDetailRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/models/$slug",
  component: ModelDetailPage,
});

const addRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/add",
  component: AddPage,
});

// /upload and /import were consolidated into /add (M8 E2). Keep the old paths
// as redirects so existing bookmarks/links still land somewhere useful.
const uploadRedirectRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/upload",
  beforeLoad: () => {
    throw redirect({ to: "/add" });
  },
  component: () => null,
});

const importRedirectRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/import",
  beforeLoad: () => {
    throw redirect({ to: "/add" });
  },
  component: () => null,
});

const collectionsRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/collections",
  validateSearch: parseCollectionsSearch,
  component: CollectionsPage,
});

// /dashboard (library stats) folded into /print/history?tab=stats (P3 Task 5).
const dashboardRedirectRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/dashboard",
  beforeLoad: () => {
    throw redirect({ to: "/print/history", search: { tab: "stats" } });
  },
  component: () => null,
});

const printHistoryRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/print/history",
  validateSearch: parsePrintHistorySearch,
  component: PrintHistoryPage,
});

const printQueueRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/print/queue",
  component: QueuePage,
});

// /queue moved under /print/queue (P3 Task 5).
const queueRedirectRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/queue",
  beforeLoad: () => {
    throw redirect({ to: "/print/queue" });
  },
  component: () => null,
});

/** Pure redirect policy for `/printer` (P3 Task 5): straight to that one
 * printer's detail page when there's exactly one visible, else back to `/`
 * (the print dashboard, which lists every printer) -- there's no single
 * sensible target otherwise. Exported for unit coverage. */
export function printerRedirect(
  printers: PrinterOut[] | undefined,
): { to: "/printers/$id"; params: { id: string } } | { to: "/" } {
  if (printers?.length === 1) {
    return { to: "/printers/$id", params: { id: String(printers[0].id) } };
  }
  return { to: "/" };
}

const printerRedirectRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/printer",
  beforeLoad: async ({ context }) => {
    const features = await context.queryClient.ensureQueryData(featuresQueryOptions);
    if (!features.printer_enabled) throw redirect({ to: "/library" });
    const printers = await context.queryClient.ensureQueryData(printersQueryOptions);
    throw redirect(printerRedirect(printers));
  },
  component: () => null,
});

const printerDetailRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/printers/$id",
  component: PrinterDetailPage,
});

const duplicatesRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/duplicates",
  component: DuplicatesPage,
});

const inventorySpoolsRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/inventory/spools",
  validateSearch: (search: Record<string, unknown>): { low?: string } => ({
    low: search.low != null ? String(search.low) : undefined,
  }),
  component: SpoolsPage,
});

const inventoryMaterialsRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/inventory/materials",
  component: MaterialsPage,
});

const inventoryProfilesRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/inventory/profiles",
  component: () => (
    <PlaceholderPage title="Profiles & recipes" phase="P6" blurb="Save slicer profiles and print recipes." />
  ),
});

const systemJobsRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/system/jobs",
  component: JobsPage,
});

// /jobs moved under /system/jobs (P3 Task 5).
const jobsRedirectRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/jobs",
  beforeLoad: () => {
    throw redirect({ to: "/system/jobs" });
  },
  component: () => null,
});

const settingsRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/settings",
  component: SettingsPage,
});

export const routeTree = rootRoute.addChildren([
  loginRoute,
  viewerWindowRoute,
  authenticatedRoute.addChildren([
    indexRoute,
    libraryRoute,
    modelDetailRoute,
    addRoute,
    uploadRedirectRoute,
    importRedirectRoute,
    collectionsRoute,
    dashboardRedirectRoute,
    printHistoryRoute,
    printQueueRoute,
    queueRedirectRoute,
    printerRedirectRoute,
    printerDetailRoute,
    duplicatesRoute,
    inventorySpoolsRoute,
    inventoryMaterialsRoute,
    inventoryProfilesRoute,
    systemJobsRoute,
    jobsRedirectRoute,
    settingsRoute,
  ]),
]);
