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
import { CameraPage } from "@/pages/CameraPage";
import { CamWallPage } from "@/pages/CamWallPage";
import { OverlayPage } from "@/pages/OverlayPage";
import { ExternalLinkPage } from "@/pages/ExternalLinkPage";
import { FinancePage } from "@/pages/FinancePage";
import { JobsPage } from "@/pages/JobsPage";
import { LibraryPage } from "@/pages/LibraryPage";
import { LoginPage } from "@/pages/LoginPage";
import { MaterialsPage } from "@/pages/MaterialsPage";
import { ModelDetailPage } from "@/pages/ModelDetailPage";
import { PrintDashboardPage } from "@/pages/PrintDashboardPage";
import { PrinterDetailPage } from "@/pages/PrinterDetailPage";
import { PrintHistoryPage } from "@/pages/PrintHistoryPage";
import { ProfilesPage } from "@/pages/ProfilesPage";
import { QueuePage } from "@/pages/QueuePage";
import { SettingsPage } from "@/pages/SettingsPage";
import { StatsPage } from "@/pages/StatsPage";
import { SpoolsPage } from "@/pages/SpoolsPage";
import { ViewerWindowPage } from "@/pages/ViewerWindowPage";
import { parseCollectionsSearch } from "@/pages/collectionsSearch";
import { parseLibrarySearch, type LibrarySearch } from "@/pages/librarySearch";
import { parsePrintHistorySearch } from "@/pages/printHistorySearch";
import { parseQueueSearch } from "@/pages/queueSearch";
import { parsePrintDashSearch } from "@/pages/printerDashSearch";
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
  validateSearch: (
    search: Record<string, unknown>,
  ): { error?: string; step?: string; token?: string; methods?: string } => ({
    error: typeof search.error === "string" ? search.error : undefined,
    step: typeof search.step === "string" ? search.step : undefined,
    token: typeof search.token === "string" ? search.token : undefined,
    methods: typeof search.methods === "string" ? search.methods : undefined,
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
  beforeLoad: (args) => sessionGuard(args),
  validateSearch: parseViewerWindowSearch,
  component: ViewerWindowPage,
});

// Q4 camera surfaces, all OUTSIDE the AppShell layout. `/camera/$id` and
// `/camwall` are session-guarded; `/overlay/$id?token=` is public (OBS
// browser source) and authenticates by its read-only overlay token only.
// Users who still must enrol in 2FA are sent to `/`, where AppShell shows the gate.
async function sessionGuard({ context }: { context: RouterContext }) {
  let me;
  try {
    me = await context.queryClient.ensureQueryData(authQueryOptions);
  } catch {
    throw redirect({ to: "/login" });
  }
  if (me.must_enroll_2fa) throw redirect({ to: "/" });
}

const cameraWindowRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/camera/$id",
  beforeLoad: sessionGuard,
  component: CameraPage,
});

const camWallRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/camwall",
  beforeLoad: sessionGuard,
  component: CamWallPage,
});

const overlayRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/overlay/$id",
  validateSearch: (search: Record<string, unknown>): { token?: string } => ({
    token: typeof search.token === "string" ? search.token : undefined,
  }),
  component: OverlayPage,
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
  // `/` is the print dashboard (its own `status|offline|q|sort` params) AND the
  // old library bookmark target (`collection|category|path`): parse both.
  validateSearch: (search: Record<string, unknown>) => ({
    ...parseLibrarySearch(search),
    ...parsePrintDashSearch(search),
  }),
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

const statsRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/stats",
  component: StatsPage,
});

const printQueueRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/print/queue",
  validateSearch: parseQueueSearch,
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
  validateSearch: (search: Record<string, unknown>): { low?: string; tab?: string } => ({
    low: search.low != null ? String(search.low) : undefined,
    tab: typeof search.tab === "string" ? search.tab : undefined,
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
  component: ProfilesPage,
});

const systemJobsRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/system/jobs",
  component: JobsPage,
});

const systemFinanceRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/system/finance",
  component: FinancePage,
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
  validateSearch: (s: Record<string, unknown>): { tab?: string } => ({
    tab: typeof s.tab === "string" ? s.tab : undefined,
  }),
  component: SettingsPage,
});

const externalLinkRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/external/$id",
  component: ExternalLinkPage,
});

export const routeTree = rootRoute.addChildren([
  loginRoute,
  viewerWindowRoute,
  cameraWindowRoute,
  camWallRoute,
  overlayRoute,
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
    systemFinanceRoute,
    jobsRedirectRoute,
    statsRoute,
    settingsRoute,
    externalLinkRoute,
  ]),
]);
