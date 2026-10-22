import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { ImageIcon } from "lucide-react";

import { ApiError } from "@/api/client";
import { useCreateImport, useImport, useImportSearch } from "@/api/imports";
import { useCreateModel } from "@/api/library";
import { UploadDropzone, type UploadTarget } from "@/components/upload/UploadDropzone";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageContainer } from "@/components/ui/page-container";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { detectSite } from "@/lib/importSites";
import { useDebouncedValue } from "@/lib/format";
import type { ImportSite, ModelDetail, SearchResult } from "@/api/types";

const TERMINAL = new Set(["done", "failed"]);

const IMPORT_SITES: ReadonlyArray<{ value: ImportSite; label: string }> = [
  { value: "thingiverse", label: "Thingiverse" },
  { value: "printables", label: "Printables" },
  { value: "makerworld", label: "MakerWorld" },
];

/** Single "Add to library" surface (M8 E2): upload your own files, import from a
 * link, or search the galleries — replacing the separate /upload and /import
 * pages and the fragile "add to existing model" picker (adding files to an
 * existing model lives on that model's page). */
export function AddPage() {
  const [activeImportId, setActiveImportId] = useState<number | undefined>(undefined);
  const active = useImport(activeImportId);

  return (
    <PageContainer width="fluid">
      <div>
        <h1 className="text-lg font-semibold text-foreground">Add to library</h1>
        <p className="text-sm text-muted-foreground">
          Upload your own files, import from a link, or search the galleries.
        </p>
      </div>

      <Tabs defaultValue="upload">
        <TabsList>
          <TabsTrigger value="upload">Upload files</TabsTrigger>
          <TabsTrigger value="url">Import from URL</TabsTrigger>
          <TabsTrigger value="search">Search galleries</TabsTrigger>
        </TabsList>
        <TabsContent value="upload">
          <UploadPanel />
        </TabsContent>
        <TabsContent value="url">
          <UrlImportCard onImportStarted={setActiveImportId} />
        </TabsContent>
        <TabsContent value="search">
          <SearchPanel onImportStarted={setActiveImportId} />
        </TabsContent>
      </Tabs>

      {active.data && <ImportProgress importId={active.data.id} />}
    </PageContainer>
  );
}

function UploadPanel() {
  const [newModelName, setNewModelName] = useState("");
  const [resolvedTarget, setResolvedTarget] = useState<UploadTarget | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const createModel = useCreateModel();
  const queryClient = useQueryClient();

  const targetReady = newModelName.trim().length > 0;

  // Resolve (and cache) the new model on the FIRST batch, then reuse it for
  // later batches instead of creating "name-2", "name-3", ... Editing the name
  // clears the cache so the next batch starts a fresh model.
  async function resolveTarget(): Promise<UploadTarget | null> {
    if (resolvedTarget) return resolvedTarget;
    let model: ModelDetail;
    try {
      model = await createModel.mutateAsync({ name: newModelName.trim() });
    } catch {
      // The global MutationCache.onError toast already surfaced the failure.
      return null;
    }
    if (!model.current_revision) return null;
    const target: UploadTarget = { modelId: model.id, revisionId: model.current_revision.id };
    setResolvedTarget(target);
    return target;
  }

  return (
    <Card className="max-w-3xl">
      <CardHeader>
        <CardTitle>Upload files</CardTitle>
        <CardDescription>Name the new model, then add files to the queue.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="new-model-name">New model name</Label>
          <Input
            id="new-model-name"
            value={newModelName}
            placeholder="e.g. Articulated dragon"
            disabled={isUploading}
            onChange={(event) => {
              setNewModelName(event.target.value);
              setResolvedTarget(null);
            }}
          />
        </div>
        <UploadDropzone
          resolveTarget={resolveTarget}
          disabled={!targetReady}
          onUploadingChange={setIsUploading}
          onUploadComplete={() => void queryClient.invalidateQueries({ queryKey: ["models"] })}
        />
      </CardContent>
    </Card>
  );
}

function UrlImportCard({ onImportStarted }: { onImportStarted: (id: number) => void }) {
  const [url, setUrl] = useState("");
  const createImport = useCreateImport();

  const detected = useMemo(() => detectSite(url), [url]);
  const canImport = detected.supported && !createImport.isPending;

  function start() {
    createImport.mutate({ url: url.trim() }, { onSuccess: (imp) => onImportStarted(imp.id) });
  }

  return (
    <Card className="max-w-3xl">
      <CardHeader>
        <CardTitle>Model URL</CardTitle>
        <CardDescription>The site is detected automatically.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="import-url">URL</Label>
          <Input
            id="import-url"
            value={url}
            placeholder="https://www.printables.com/model/3161-benchy"
            onChange={(event) => setUrl(event.target.value)}
          />
        </div>

        {url.trim() !== "" && detected.site !== null && (
          <div className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">Detected:</span>
            <Badge variant={detected.supported ? "secondary" : "outline"}>{detected.label}</Badge>
          </div>
        )}

        {url.trim() !== "" && detected.site === null && (
          <p role="alert" className="text-sm text-destructive">
            Unrecognized link — paste a Thingiverse, Printables, or MakerWorld model URL.
          </p>
        )}

        <Button type="button" onClick={start} disabled={!canImport}>
          {createImport.isPending ? "Starting…" : "Import"}
        </Button>

        {createImport.isError && (
          <p role="alert" className="text-sm text-destructive">
            {createImport.error instanceof ApiError ? createImport.error.detail : "Could not start the import."}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function SearchPanel({ onImportStarted }: { onImportStarted: (id: number) => void }) {
  const [selectedSites, setSelectedSites] = useState<ImportSite[]>(IMPORT_SITES.map((s) => s.value));
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query, 300);
  const search = useImportSearch(selectedSites, debouncedQuery);
  const createImport = useCreateImport();
  const [pendingUrl, setPendingUrl] = useState<string | null>(null);

  const results = useMemo(
    () => search.data?.pages.flatMap((page) => page.results) ?? [],
    [search.data],
  );
  const perSite = search.data?.pages.at(-1)?.per_site ?? [];

  function toggleSite(site: ImportSite) {
    setSelectedSites((prev) => (prev.includes(site) ? prev.filter((s) => s !== site) : [...prev, site]));
  }

  function addToLibrary(result: SearchResult) {
    setPendingUrl(result.url);
    createImport.mutate(
      { url: result.url },
      {
        onSuccess: (imp) => {
          setPendingUrl(null);
          onImportStarted(imp.id);
        },
        onError: () => setPendingUrl(null),
      },
    );
  }

  const noSites = selectedSites.length === 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Search galleries</CardTitle>
        <CardDescription>Search all selected sites at once, then import a result.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Sites to search">
          {IMPORT_SITES.map((s) => {
            const on = selectedSites.includes(s.value);
            return (
              <button key={s.value} type="button" aria-pressed={on} onClick={() => toggleSite(s.value)}>
                <Badge variant={on ? "default" : "outline"} className="cursor-pointer">
                  {s.label}
                </Badge>
              </button>
            );
          })}
        </div>

        <div className="flex max-w-3xl flex-col gap-1.5">
          <Label htmlFor="search-query">Search query</Label>
          <Input
            id="search-query"
            value={query}
            placeholder="Search models…"
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>

        {perSite
          .filter((s) => s.status === "error")
          .map((s) => (
            <p key={s.site} role="alert" className="text-sm text-destructive">
              {s.site}: {s.detail ?? "search failed"}
            </p>
          ))}

        {noSites || debouncedQuery.trim() === "" ? (
          <p className="text-sm text-muted-foreground">
            {noSites ? "Select at least one site to search." : "Type to search."}
          </p>
        ) : search.isLoading ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
            {Array.from({ length: 6 }, (_, i) => (
              <Skeleton key={i} className="h-44 w-full rounded-lg" />
            ))}
          </div>
        ) : search.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {search.error instanceof ApiError ? search.error.detail : "Search failed."}
          </p>
        ) : results.length === 0 ? (
          <p className="text-sm text-muted-foreground">No results for &quot;{debouncedQuery}&quot;.</p>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
              {results.map((result) => (
                <SearchResultCard
                  key={`${result.site}-${result.external_id}`}
                  result={result}
                  busy={createImport.isPending && pendingUrl === result.url}
                  onAdd={() => addToLibrary(result)}
                />
              ))}
            </div>
            {search.hasNextPage && (
              <div className="flex justify-center">
                <Button
                  type="button"
                  variant="outline"
                  disabled={search.isFetchingNextPage}
                  onClick={() => void search.fetchNextPage()}
                >
                  {search.isFetchingNextPage ? "Loading…" : "Load more"}
                </Button>
              </div>
            )}
          </>
        )}

        {createImport.isError && (
          <p role="alert" className="text-sm text-destructive">
            {createImport.error instanceof ApiError ? createImport.error.detail : "Could not start the import."}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function SearchResultCard({
  result,
  busy,
  onAdd,
}: {
  result: SearchResult;
  busy: boolean;
  onAdd: () => void;
}) {
  const [imgErrored, setImgErrored] = useState(false);
  const showThumb = result.thumbnail_url !== null && !imgErrored;

  return (
    <Card className="gap-2 overflow-hidden" size="sm">
      <div className="relative flex aspect-square items-center justify-center bg-muted">
        {showThumb ? (
          <img
            src={result.thumbnail_url ?? undefined}
            alt={result.title}
            className="h-full w-full object-cover"
            onError={() => setImgErrored(true)}
          />
        ) : (
          <ImageIcon className="size-8 text-muted-foreground" />
        )}
        <Badge variant="secondary" className="absolute top-1.5 left-1.5 capitalize backdrop-blur-sm">
          {result.site}
        </Badge>
      </div>
      <CardContent className="flex flex-col gap-1.5">
        <h3 className="truncate text-sm font-medium" title={result.title}>
          {result.title}
        </h3>
        {result.author && <p className="truncate text-xs text-muted-foreground">by {result.author}</p>}
        <Button type="button" size="sm" disabled={busy} onClick={onAdd}>
          {busy ? "Adding…" : "Add to library"}
        </Button>
      </CardContent>
    </Card>
  );
}

function ImportProgress({ importId }: { importId: number }) {
  const imp = useImport(importId);
  if (!imp.data) return null;
  const { state, error, model_id } = imp.data;
  const done = TERMINAL.has(state);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Import progress</CardTitle>
        <CardDescription>
          {state === "done" ? "Complete." : done ? "Failed." : `${state}…`}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {!done && (
          <div className="h-2 w-full overflow-hidden rounded bg-muted">
            <div className="h-full w-1/2 animate-pulse rounded bg-primary" />
          </div>
        )}
        {state === "failed" && (
          <p role="alert" className="text-sm text-destructive">
            {error ?? "Import failed."}
          </p>
        )}
        {state === "done" && model_id !== null && (
          <Button asChild variant="outline">
            <Link to="/library">View library</Link>
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
