import type { QueryClient } from "@tanstack/react-query";
import { Outlet, createRootRouteWithContext, createRoute, redirect } from "@tanstack/react-router";

import { authQueryOptions } from "@/api/auth";
import { AppShell } from "@/components/AppShell";
import { AddPage } from "@/pages/AddPage";
import { CollectionsPage } from "@/pages/CollectionsPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { DuplicatesPage } from "@/pages/DuplicatesPage";
import { JobsPage } from "@/pages/JobsPage";
import { LibraryPage } from "@/pages/LibraryPage";
import { LoginPage } from "@/pages/LoginPage";
import { ModelDetailPage } from "@/pages/ModelDetailPage";
import { PrinterPage } from "@/pages/PrinterPage";
import { QueuePage } from "@/pages/QueuePage";
import { SettingsPage } from "@/pages/SettingsPage";
import { ViewerWindowPage } from "@/pages/ViewerWindowPage";
import { parseCollectionsSearch } from "@/pages/collectionsSearch";
import { parseLibrarySearch } from "@/pages/librarySearch";
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

const libraryRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/",
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

const dashboardRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/dashboard",
  component: DashboardPage,
});

const printerRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/printer",
  component: PrinterPage,
});

const jobsRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/jobs",
  component: JobsPage,
});

const queueRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/queue",
  component: QueuePage,
});

const duplicatesRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "/duplicates",
  component: DuplicatesPage,
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
    libraryRoute,
    modelDetailRoute,
    addRoute,
    uploadRedirectRoute,
    importRedirectRoute,
    collectionsRoute,
    dashboardRoute,
    printerRoute,
    jobsRoute,
    queueRoute,
    duplicatesRoute,
    settingsRoute,
  ]),
]);
