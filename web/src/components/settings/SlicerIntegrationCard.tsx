/**
 * Slicer integration card (Round 8 T6): documents the two OrcaSlicer ->
 * library loops and mints/lists/revokes the API tokens they authenticate
 * with.
 *
 * Token section reuses the exact mint/reveal/list/revoke UX and hooks
 * `BrowserExtensionCard` (M10 Workstream C) built for the browser
 * extension -- it's the SAME `ApiToken` bearer plane
 * (`app/api/ext.py`'s `require_api_token`), which `app/api/slicer.py`'s
 * intake endpoint also depends on (Round 8 T4), so a token minted from
 * either card authenticates both. The mint/reveal/list/revoke subcomponents
 * below are a deliberate copy rather than a shared import -- every other
 * Settings card (`PrintablesAccountCard`/`BambuAccountCard`) is similarly
 * self-contained, and the two cards' label inputs need distinct DOM ids
 * since both render on the same Accounts tab at once.
 *
 * Script download: `web/public/slicer_postprocess.py` IS served in
 * production (`docker/Dockerfile` builds `web/dist`, which Vite populates
 * with `public/`'s contents verbatim, and `app/static.py` serves any real
 * file under the static root -- confirmed in the Round 8 T4 report). But
 * `app/static.py` sends every non-`index.html` static file with a
 * year-long `Cache-Control: immutable` header, and this script isn't
 * content-hashed like Vite's JS/CSS bundles, so a browser that downloaded
 * it once could keep serving a stale copy for up to a year after an app
 * update. The download link appends `?v=<timestamp>` (recomputed on every
 * render, so a fresh page load always mints a new value) to bust that
 * cache -- there's no build id exposed to the frontend to use instead.
 */
import { useState } from "react";
import { CheckIcon, CopyIcon, DownloadIcon } from "lucide-react";

import { ApiError } from "@/api/client";
import { useApiTokens, useCreateApiToken, useRevokeApiToken } from "@/api/apiTokens";
import { useFeatures } from "@/api/features";
import { useSlicerHealth } from "@/api/slicing";
import type { ApiTokenOut } from "@/api/types";
import { SyncProfilesButton } from "@/components/inventory/SyncProfilesButton";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDate } from "@/lib/format";

export function SlicerIntegrationCard() {
  const tokens = useApiTokens();
  const health = useSlicerHealth();

  return (
    <Card>
      <CardHeader>
        <CardTitle>Slicer integration</CardTitle>
        <CardDescription>Send sliced files straight from OrcaSlicer to your library.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">
            Uses the same API tokens as the browser extension — a token from either works for both.
          </p>
          <MintForm />
          {tokens.isLoading ? (
            <Skeleton className="h-16 w-full rounded-lg" />
          ) : tokens.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {tokens.error instanceof ApiError ? tokens.error.detail : "Couldn't load API tokens."}
            </p>
          ) : (
            <TokenList tokens={tokens.data ?? []} />
          )}
        </div>

        <div className="space-y-4 border-t border-border pt-4 text-sm">
          <div className="space-y-1">
            <h3 className="font-medium text-foreground">1. Auto-upload every slice (metadata)</h3>
            <p className="text-muted-foreground">
              OrcaSlicer → <strong>Process → Others → Post-processing scripts</strong>:{" "}
              <code className="rounded bg-muted px-1 py-0.5 text-xs">python3 /path/to/slicer_postprocess.py</code>,
              with <code className="rounded bg-muted px-1 py-0.5 text-xs">INTAKE_URL</code> (e.g.{" "}
              <code className="rounded bg-muted px-1 py-0.5 text-xs">
                http://&lt;this host&gt;:8080/api/slicer/intake
              </code>
              ) and <code className="rounded bg-muted px-1 py-0.5 text-xs">INTAKE_TOKEN</code> set in the
              script&apos;s environment. Every sliced plate is uploaded and matched to a model by name (or a new
              model is created).
            </p>
            <p className="text-xs text-muted-foreground">
              This path uploads plain .gcode — rich history and metadata, but not printable from this app.
            </p>
          </div>
          <div className="space-y-1">
            <h3 className="font-medium text-foreground">2. Printable file (watched folder)</h3>
            <p className="text-muted-foreground">
              <strong>File → Export → Export plate sliced file</strong> into the watched folder — imported the same
              way, and the resulting .gcode.3mf gets the Send-to-printer button (Queue + Files tab).
            </p>
          </div>
          <ScriptDownloadLink />
          <WatchedFolderStatus />
        </div>

        {health.data?.configured ? (
          <div className="space-y-2 border-t border-border pt-4">
            <h3 className="text-sm font-medium text-foreground">Slicer profiles</h3>
            <p className="text-xs text-muted-foreground">
              Import the slicer sidecar&apos;s bundled Bambu printer, process and filament presets
              (version {health.data.version ?? "?"}). Edited or imported profiles are never overwritten.
            </p>
            <SyncProfilesButton idPrefix="card-sync" />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ScriptDownloadLink() {
  // Recomputed every render (not memoized) so each fresh page load/render
  // mints a new cache-busting query value -- see the module docstring.
  const href = `/slicer_postprocess.py?v=${Date.now()}`;

  return (
    <a
      href={href}
      download="slicer_postprocess.py"
      className="inline-flex items-center gap-1.5 text-sm font-medium text-foreground underline underline-offset-4 hover:no-underline"
    >
      <DownloadIcon className="size-4" /> Download slicer_postprocess.py
    </a>
  );
}

function WatchedFolderStatus() {
  const features = useFeatures();

  if (features.isLoading) return <Skeleton className="h-10 w-full rounded-lg" />;

  if (!features.data?.watch_enabled) {
    return (
      <p className="text-xs text-muted-foreground">
        Watched-folder polling is off — set a poll interval under Settings → General → Automation.
      </p>
    );
  }

  return (
    <div className="space-y-0.5">
      <p className="text-sm text-foreground">
        Watched folder: <code className="rounded bg-muted px-1 py-0.5 text-xs">{features.data.watch_dir}</code>{" "}
        (active)
      </p>
      <p className="text-xs text-muted-foreground">
        That&apos;s the path inside the container — it maps to{" "}
        <code className="rounded bg-muted px-1 py-0.5">WATCH_HOST_DIR</code> on the host.
      </p>
    </div>
  );
}

function MintForm() {
  const [label, setLabel] = useState("OrcaSlicer");
  const [minted, setMinted] = useState<{ label: string; token: string } | null>(null);
  const createToken = useCreateApiToken();

  function submit() {
    const trimmed = label.trim();
    createToken.mutate(
      { label: trimmed },
      {
        onSuccess: (result) => {
          setMinted({ label: result.label, token: result.token });
          setLabel("");
        },
      },
    );
  }

  if (minted) {
    return <MintedTokenReveal label={minted.label} token={minted.token} onDismiss={() => setMinted(null)} />;
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="flex flex-1 flex-col gap-1.5">
          <Label htmlFor="slicer-api-token-label">Token label</Label>
          <Input
            id="slicer-api-token-label"
            placeholder="e.g. OrcaSlicer"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            disabled={createToken.isPending}
          />
        </div>
        <Button type="button" disabled={createToken.isPending || !label.trim()} onClick={submit}>
          {createToken.isPending ? "Creating…" : "Create token"}
        </Button>
      </div>
      {createToken.isError && (
        <p role="alert" className="text-sm text-destructive">
          {createToken.error instanceof ApiError ? createToken.error.detail : "Could not create the token."}
        </p>
      )}
    </div>
  );
}

function MintedTokenReveal({
  label,
  token,
  onDismiss,
}: {
  label: string;
  token: string;
  onDismiss: () => void;
}) {
  const [copied, setCopied] = useState(false);

  function handleCopy() {
    void navigator.clipboard.writeText(token).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <div className="space-y-2 rounded-lg border border-warning/40 bg-warning/10 p-3">
      <p className="text-sm font-medium text-foreground">Token created for &quot;{label}&quot;</p>
      <p className="text-xs text-warning">
        Copy it now — it won&apos;t be shown again. Set it as <code>INTAKE_TOKEN</code> in the
        post-processing script&apos;s environment.
      </p>
      <div className="flex items-center gap-2">
        <code aria-label="New API token" className="min-w-0 flex-1 truncate rounded bg-muted px-2 py-1.5 font-mono text-xs">
          {token}
        </code>
        <Button type="button" size="sm" variant="outline" onClick={handleCopy}>
          {copied ? (
            <>
              <CheckIcon /> Copied
            </>
          ) : (
            <>
              <CopyIcon /> Copy
            </>
          )}
        </Button>
      </div>
      <Button type="button" size="sm" variant="ghost" onClick={onDismiss}>
        Done — I&apos;ve saved it
      </Button>
    </div>
  );
}

function TokenList({ tokens }: { tokens: ApiTokenOut[] }) {
  if (tokens.length === 0) {
    return <p className="text-sm text-muted-foreground">No tokens yet.</p>;
  }

  return (
    <ul className="divide-y divide-border">
      {tokens.map((token) => (
        <TokenRow key={token.id} token={token} />
      ))}
    </ul>
  );
}

function TokenRow({ token }: { token: ApiTokenOut }) {
  const revokeToken = useRevokeApiToken();
  const revokingThis = revokeToken.isPending && revokeToken.variables === token.id;
  const errorForThis = revokeToken.isError && revokeToken.variables === token.id;

  return (
    <li className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <p className="text-sm font-medium text-foreground">{token.label}</p>
        <p className="text-xs text-muted-foreground">
          Created {formatDate(token.created_at)} ·{" "}
          {token.last_used_at ? `Last used ${formatDate(token.last_used_at)}` : "Never used"}
        </p>
        {errorForThis && (
          <p role="alert" className="mt-1 text-xs text-destructive">
            {revokeToken.error instanceof ApiError ? revokeToken.error.detail : "Could not revoke this token."}
          </p>
        )}
      </div>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={revokingThis}
        onClick={() => revokeToken.mutate(token.id)}
      >
        {revokingThis ? "Revoking…" : "Revoke"}
      </Button>
    </li>
  );
}
