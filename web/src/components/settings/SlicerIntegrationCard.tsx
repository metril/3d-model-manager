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
import { Trans, useTranslation } from "react-i18next";
import { CheckIcon, CopyIcon, DownloadIcon } from "lucide-react";

import { ApiError } from "@/api/client";
import { useApiTokens, useCreateApiToken, useRevokeApiToken } from "@/api/apiTokens";
import { useFeatures } from "@/api/features";
import { useSlicerHealth } from "@/api/slicing";
import type { ApiTokenOut } from "@/api/types";
import { SyncProfilesButton } from "@/components/inventory/SyncProfilesButton";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDate } from "@/lib/format";

export function SlicerIntegrationCard() {
  const { t } = useTranslation();
  const tokens = useApiTokens();
  const health = useSlicerHealth();

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settingsSlicer.title")}</CardTitle>
        <CardDescription>{t("settingsSlicer.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">
            {t("settingsSlicer.tokensNote")}
          </p>
          <MintForm />
          {tokens.isLoading ? (
            <Skeleton className="h-16 w-full rounded-lg" />
          ) : tokens.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {tokens.error instanceof ApiError ? tokens.error.detail : t("settingsSlicer.tokensError")}
            </p>
          ) : (
            <TokenList tokens={tokens.data ?? []} />
          )}
        </div>

        <div className="space-y-4 border-t border-border pt-4 text-sm">
          <div className="space-y-1">
            <h3 className="font-medium text-foreground">{t("settingsSlicer.step1Title")}</h3>
            <p className="text-muted-foreground">
              <Trans
                i18nKey="settingsSlicer.step1Body"
                values={{ url: "http://<this host>:8080/api/slicer/intake" }}
                components={{ strong: <strong />, code: <code className="rounded bg-muted px-1 py-0.5 text-xs" /> }}
              />
            </p>
            <p className="text-xs text-muted-foreground">
              {t("settingsSlicer.step1Note")}
            </p>
          </div>
          <div className="space-y-1">
            <h3 className="font-medium text-foreground">{t("settingsSlicer.step2Title")}</h3>
            <p className="text-muted-foreground">
              <Trans i18nKey="settingsSlicer.step2Body" components={{ strong: <strong /> }} />
            </p>
          </div>
          <ScriptDownloadLink />
          <WatchedFolderStatus />
        </div>

        {health.data?.configured ? (
          <div className="space-y-2 border-t border-border pt-4">
            <h3 className="text-sm font-medium text-foreground">{t("settingsSlicer.profiles")}</h3>
            <p className="text-xs text-muted-foreground">
              {t("settingsSlicer.profilesBody", { version: health.data.version ?? "?" })}
            </p>
            <SyncProfilesButton idPrefix="card-sync" />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ScriptDownloadLink() {
  const { t } = useTranslation();
  // Recomputed every render (not memoized) so each fresh page load/render
  // mints a new cache-busting query value -- see the module docstring.
  const href = `/slicer_postprocess.py?v=${Date.now()}`;

  return (
    <a
      href={href}
      download="slicer_postprocess.py"
      className="inline-flex items-center gap-1.5 text-sm font-medium text-foreground underline underline-offset-4 hover:no-underline"
    >
      <DownloadIcon className="size-4" /> {t("settingsSlicer.download")}
    </a>
  );
}

function WatchedFolderStatus() {
  const { t } = useTranslation();
  const features = useFeatures();

  if (features.isLoading) return <Skeleton className="h-10 w-full rounded-lg" />;

  if (!features.data?.watch_enabled) {
    return (
      <p className="text-xs text-muted-foreground">
        {t("settingsSlicer.pollingOff")}
      </p>
    );
  }

  return (
    <div className="space-y-0.5">
      <p className="text-sm text-foreground">
        <Trans
          i18nKey="settingsSlicer.watchedFolder"
          values={{ dir: features.data.watch_dir }}
          components={{ code: <code className="rounded bg-muted px-1 py-0.5 text-xs" /> }}
        />
      </p>
      <p className="text-xs text-muted-foreground">
        <Trans
          i18nKey="settingsSlicer.watchedFolderHint"
          components={{ code: <code className="rounded bg-muted px-1 py-0.5" /> }}
        />
      </p>
    </div>
  );
}

function MintForm() {
  const { t } = useTranslation();
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
          <Label htmlFor="slicer-api-token-label">{t("settingsSlicer.tokenLabel")}</Label>
          <Input
            id="slicer-api-token-label"
            placeholder={t("settingsSlicer.tokenPlaceholder")}
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            disabled={createToken.isPending}
          />
        </div>
        <Button type="button" disabled={createToken.isPending || !label.trim()} onClick={submit}>
          {createToken.isPending ? t("settingsSlicer.creating") : t("settingsSlicer.create")}
        </Button>
      </div>
      {createToken.isError && (
        <p role="alert" className="text-sm text-destructive">
          {createToken.error instanceof ApiError ? createToken.error.detail : t("settingsSlicer.createError")}
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
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  function handleCopy() {
    void navigator.clipboard.writeText(token).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <div className="space-y-2 rounded-lg border border-warning/40 bg-warning/10 p-3">
      <p className="text-sm font-medium text-foreground">{t("settingsSlicer.created", { label })}</p>
      <p className="text-xs text-warning">
        <Trans i18nKey="settingsSlicer.copyNow" components={{ code: <code /> }} />
      </p>
      <div className="flex items-center gap-2">
        <code aria-label={t("settingsSlicer.newToken")} className="min-w-0 flex-1 truncate rounded bg-muted px-2 py-1.5 font-mono text-xs">
          {token}
        </code>
        <Button type="button" size="sm" variant="outline" onClick={handleCopy}>
          {copied ? (
            <>
              <CheckIcon /> {t("settingsSlicer.copied")}
            </>
          ) : (
            <>
              <CopyIcon /> {t("settingsSlicer.copy")}
            </>
          )}
        </Button>
      </div>
      <Button type="button" size="sm" variant="ghost" onClick={onDismiss}>
        {t("settingsSlicer.saved")}
      </Button>
    </div>
  );
}

function TokenList({ tokens }: { tokens: ApiTokenOut[] }) {
  const { t } = useTranslation();
  if (tokens.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("settingsSlicer.noTokens")}</p>;
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
  const { t } = useTranslation();
  const [confirming, setConfirming] = useState(false);
  const revokeToken = useRevokeApiToken();
  const revokingThis = revokeToken.isPending && revokeToken.variables === token.id;
  const errorForThis = revokeToken.isError && revokeToken.variables === token.id;

  return (
    <li className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <p className="text-sm font-medium text-foreground">{token.label}</p>
        <p className="text-xs text-muted-foreground">
          {t("settingsSlicer.createdOn", { date: formatDate(token.created_at) })} ·{" "}
          {token.last_used_at
            ? t("settingsSlicer.lastUsed", { date: formatDate(token.last_used_at) })
            : t("settingsSlicer.neverUsed")}
        </p>
        {errorForThis && (
          <p role="alert" className="mt-1 text-xs text-destructive">
            {revokeToken.error instanceof ApiError ? revokeToken.error.detail : t("settingsSlicer.revokeError")}
          </p>
        )}
      </div>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={revokingThis}
        onClick={() => setConfirming(true)}
      >
        {revokingThis ? t("settingsSlicer.revoking") : t("common.revoke")}
      </Button>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={t("tokens.revokeTitle", { label: token.label })}
        description={t("tokens.revokeDesc")}
        confirmLabel={t("tokens.revoke")}
        cancelLabel={t("common.cancel")}
        destructive
        onConfirm={() => revokeToken.mutate(token.id)}
      />
    </li>
  );
}
