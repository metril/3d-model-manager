/**
 * Browser-extension API token card (M10 Workstream C task C): mint, list,
 * and revoke the app API tokens the sideloaded browser extension's options
 * page authenticates with (`app/api/ext.py`'s bearer-token auth plane --
 * separate from the session cookie every other Settings card relies on).
 *
 * Minting shows the plaintext token exactly once (`ApiTokenMintOut`) --
 * the list endpoint (`ApiTokenOut`) never carries it or its hash again, so
 * there is no "reveal" affordance for a previously-minted token; losing one
 * means revoking it and minting a fresh one.
 */
import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import { CheckIcon, CopyIcon } from "lucide-react";

import { ApiError } from "@/api/client";
import { useApiTokens, useCreateApiToken, useRevokeApiToken } from "@/api/apiTokens";
import type { ApiTokenOut } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDate } from "@/lib/format";

export function BrowserExtensionCard() {
  const { t } = useTranslation();
  const tokens = useApiTokens();

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settingsBrowserExt.title")}</CardTitle>
        <CardDescription>
          <Trans i18nKey="settingsBrowserExt.desc" components={{ code: <code /> }} />
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <MintForm />
        {tokens.isLoading ? (
          <Skeleton className="h-16 w-full rounded-lg" />
        ) : tokens.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {tokens.error instanceof ApiError ? tokens.error.detail : t("settingsBrowserExt.loadError")}
          </p>
        ) : (
          <TokenList tokens={tokens.data ?? []} />
        )}
      </CardContent>
    </Card>
  );
}

function MintForm() {
  const { t } = useTranslation();
  const [label, setLabel] = useState("");
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
          <Label htmlFor="api-token-label">{t("settingsBrowserExt.tokenLabel")}</Label>
          <Input
            id="api-token-label"
            placeholder={t("settingsBrowserExt.labelPlaceholder")}
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            disabled={createToken.isPending}
          />
        </div>
        <Button type="button" disabled={createToken.isPending || !label.trim()} onClick={submit}>
          {createToken.isPending ? t("settingsBrowserExt.creating") : t("settingsBrowserExt.create")}
        </Button>
      </div>
      {createToken.isError && (
        <p role="alert" className="text-sm text-destructive">
          {createToken.error instanceof ApiError ? createToken.error.detail : t("settingsBrowserExt.createError")}
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
      <p className="text-sm font-medium text-foreground">{t("settingsBrowserExt.created", { label })}</p>
      <p className="text-xs text-warning">
        {t("settingsBrowserExt.copyNow")}
      </p>
      <div className="flex items-center gap-2">
        <code aria-label={t("settingsBrowserExt.newToken")} className="min-w-0 flex-1 truncate rounded bg-muted px-2 py-1.5 font-mono text-xs">
          {token}
        </code>
        <Button type="button" size="sm" variant="outline" onClick={handleCopy}>
          {copied ? (
            <>
              <CheckIcon /> {t("settingsBrowserExt.copied")}
            </>
          ) : (
            <>
              <CopyIcon /> {t("settingsBrowserExt.copy")}
            </>
          )}
        </Button>
      </div>
      <Button type="button" size="sm" variant="ghost" onClick={onDismiss}>
        {t("settingsBrowserExt.done")}
      </Button>
    </div>
  );
}

function TokenList({ tokens }: { tokens: ApiTokenOut[] }) {
  const { t } = useTranslation();
  if (tokens.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("settingsBrowserExt.none")}</p>;
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
          {t("settingsBrowserExt.createdAt", { date: formatDate(token.created_at) })} ·{" "}
          {token.last_used_at ? t("settingsBrowserExt.lastUsed", { date: formatDate(token.last_used_at) }) : t("settingsBrowserExt.neverUsed")}
        </p>
        {errorForThis && (
          <p role="alert" className="mt-1 text-xs text-destructive">
            {revokeToken.error instanceof ApiError ? revokeToken.error.detail : t("settingsBrowserExt.revokeError")}
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
        {revokingThis ? t("settingsBrowserExt.revoking") : t("common.revoke")}
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
