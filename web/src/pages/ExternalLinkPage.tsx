import { getRouteApi } from "@tanstack/react-router";
import { ExternalLink as ExternalLinkIcon } from "lucide-react";

import { useExternalLinks } from "@/api/externalLinks";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

const routeApi = getRouteApi("/authenticated/external/$id");

/** Renders an admin-configured link: embedded for `iframe` mode, or an
 * "open in new tab" prompt for `newtab` (also the fallback when the target
 * site refuses to be framed -- the toolbar button is always available). */
export function ExternalLinkPage() {
  const { id } = routeApi.useParams();
  const links = useExternalLinks();

  if (links.isLoading) return <Skeleton className="h-64 w-full rounded-xl" />;
  const link = links.data?.find((candidate) => String(candidate.id) === id);
  if (!link) {
    return (
      <Card className="mx-auto mt-12 max-w-md">
        <CardHeader className="items-center text-center">
          <CardTitle>Link not found</CardTitle>
          <CardDescription>This external link was removed or never existed.</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const openButton = (
    <Button asChild variant="outline" size="sm">
      <a href={link.url} target="_blank" rel="noopener noreferrer">
        <ExternalLinkIcon className="size-4" />
        Open in new tab
      </a>
    </Button>
  );

  if (link.open_mode === "newtab") {
    return (
      <Card className="mx-auto mt-12 max-w-md">
        <CardHeader className="items-center text-center">
          <CardTitle>{link.name}</CardTitle>
          <CardDescription>This link opens in a new tab.</CardDescription>
          {openButton}
        </CardHeader>
      </Card>
    );
  }

  return (
    <div className="flex h-[calc(100svh-7rem)] flex-col gap-2">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">{link.name}</h1>
        {openButton}
      </div>
      <iframe
        title={link.name}
        src={link.url}
        className="w-full flex-1 rounded-lg border border-border bg-background"
        referrerPolicy="no-referrer"
        allow="fullscreen"
      />
    </div>
  );
}
