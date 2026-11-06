import { createRouter } from "@tanstack/react-router";

import { queryClient } from "@/queryClient";
import { RouteError, RouteNotFound } from "@/components/RouteFallbacks";
import { RoutePending } from "@/components/RoutePending";
import { routeTree } from "@/routes";

export const router = createRouter({
  routeTree,
  context: { queryClient },
  defaultPreload: "intent",
  defaultPendingMs: 150,
  defaultPendingComponent: RoutePending,
  defaultNotFoundComponent: RouteNotFound,
  defaultErrorComponent: RouteError,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
