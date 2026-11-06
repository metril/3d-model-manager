import { Link, type ErrorComponentProps } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";

/** Rendered by the router for an unmatched URL or a thrown `notFound()`.
 * Nested routes render it inside the app shell's outlet. */
export function RouteNotFound() {
  const { t } = useTranslation();
  return (
    <div role="alert" className="flex min-h-40 flex-col items-center justify-center gap-3 p-8 text-center">
      <h1 className="text-lg font-semibold">{t("routeError.notFoundTitle")}</h1>
      <p className="text-sm text-muted-foreground">{t("routeError.notFoundBody")}</p>
      <Button asChild variant="outline" size="sm">
        <Link to="/">{t("routeError.home")}</Link>
      </Button>
    </div>
  );
}

/** Rendered when a route's loader/component throws. A 404 from the API reads
 * as "not found"; anything else offers a retry. */
export function RouteError({ error, reset }: ErrorComponentProps) {
  const { t } = useTranslation();
  if (error instanceof ApiError && error.status === 404) return <RouteNotFound />;
  const message = error instanceof ApiError ? error.detail : error instanceof Error ? error.message : "";
  return (
    <div role="alert" className="flex min-h-40 flex-col items-center justify-center gap-3 p-8 text-center">
      <h1 className="text-lg font-semibold">{t("routeError.title")}</h1>
      {message ? <p className="max-w-prose text-sm text-muted-foreground">{message}</p> : null}
      <div className="flex gap-2">
        <Button size="sm" onClick={reset}>
          {t("routeError.retry")}
        </Button>
        <Button asChild variant="outline" size="sm">
          <Link to="/">{t("routeError.home")}</Link>
        </Button>
      </div>
    </div>
  );
}
