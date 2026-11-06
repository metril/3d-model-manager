/**
 * Bambu Lab account connect card (Workstream B task B3; mirrors
 * `SiteTokensCard`'s masked-secret card style and `PrinterSetupCard`'s
 * draft-state/pending-disable/`role="alert"` conventions). Connecting an
 * account powers MakerWorld's authenticated search + file downloads --
 * anonymous MakerWorld access only sees trending results and can't download
 * files (see `backend/app/services/bambu_auth.py`).
 *
 * The password field is write-only: it makes one login POST and is never
 * echoed back by any response. No token is ever rendered here -- status
 * only ever carries `connected`/`account`/`region` (`BambuStatusOut`), never
 * an access/refresh token.
 */
import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";

import { useBambuDisconnect, useBambuLogin, useBambuStatus, useBambuVerify } from "@/api/bambu";
import { ApiError } from "@/api/client";
import type { BambuRegion } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

const REGIONS: ReadonlyArray<{ value: BambuRegion; labelKey: string }> = [
  { value: "global", labelKey: "settingsBambu.regionGlobal" },
  { value: "china", labelKey: "settingsBambu.regionChina" },
];

export function BambuAccountCard({ className }: { className?: string }) {
  const { t } = useTranslation();
  const status = useBambuStatus();
  // `connected` stays true across a session expiry (the refresh token row is
  // still on file -- only the refresh ATTEMPT failed), so `needs_reconnect`
  // is what actually decides whether to offer the login form again here
  // (import-health task T3; backend/app/services/bambu_auth.py).
  const needsReconnect = status.data?.needs_reconnect ?? false;

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>{t("settingsBambu.title")}</CardTitle>
        <CardDescription>
          {t("settingsBambu.desc")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {status.isLoading ? (
          <Skeleton className="h-32 w-full rounded-lg" />
        ) : status.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {status.error instanceof ApiError ? status.error.detail : t("settingsBambu.statusError")}
          </p>
        ) : (
          <>
            {needsReconnect ? (
              <div role="alert" className="rounded-lg border border-destructive/50 bg-destructive/10 p-3">
                <p className="text-sm font-medium text-destructive">{t("settingsBambu.expired")}</p>
                <p className="text-sm text-destructive">{t("settingsBambu.expiredHint")}</p>
              </div>
            ) : null}
            {status.data?.connected && !needsReconnect ? (
              <ConnectedView account={status.data.account} />
            ) : (
              <LoginForm initialAccount={needsReconnect ? status.data?.account : null} />
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function ConnectedView({ account }: { account: string | null }) {
  const { t } = useTranslation();
  const disconnect = useBambuDisconnect();

  return (
    <div className="space-y-3">
      <p className="text-sm text-foreground">
        <Trans
          i18nKey="settingsBambu.connectedAs"
          values={{ account: account ?? t("settingsBambu.unknownAccount") }}
          components={{ acct: <span className="font-medium" /> }}
        />
      </p>
      <Button type="button" variant="outline" disabled={disconnect.isPending} onClick={() => disconnect.mutate()}>
        {disconnect.isPending ? t("settingsBambu.disconnecting") : t("settingsBambu.disconnect")}
      </Button>
      {disconnect.isError && (
        <p role="alert" className="text-sm text-destructive">
          {disconnect.error instanceof ApiError ? disconnect.error.detail : t("settingsBambu.disconnectError")}
        </p>
      )}
    </div>
  );
}

function LoginForm({ initialAccount = null }: { initialAccount?: string | null } = {}) {
  const { t } = useTranslation();
  const [account, setAccount] = useState(initialAccount ?? "");
  const [password, setPassword] = useState("");
  const [region, setRegion] = useState<BambuRegion>("global");
  const [code, setCode] = useState("");
  // Opaque continuation carried from a `mfa_required` login response into
  // the follow-up verify call -- `BambuLoginOut.mfa_context` echoed back as
  // `BambuVerifyIn.mfa_context` (backend/app/schemas/settings.py). Never a
  // secret itself, just whatever Bambu's login response needs to complete
  // the challenge.
  const [mfaContext, setMfaContext] = useState<Record<string, unknown> | null>(null);

  const login = useBambuLogin();
  const verify = useBambuVerify();

  const mfaRequired = mfaContext !== null;
  const busy = login.isPending || verify.isPending;

  function submitLogin() {
    login.mutate(
      { account: account.trim(), password, region },
      {
        onSuccess: (result) => {
          setPassword("");
          if (result.status === "mfa_required") setMfaContext(result.mfa_context ?? {});
        },
      },
    );
  }

  function submitVerify() {
    verify.mutate(
      { account: account.trim(), code: code.trim(), region, mfa_context: mfaContext ?? {} },
      { onSuccess: () => setCode("") },
    );
  }

  function startOver() {
    setMfaContext(null);
    setCode("");
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="bambu-account">{t("settingsBambu.email")}</Label>
          <Input
            id="bambu-account"
            type="email"
            value={account}
            onChange={(e) => setAccount(e.target.value)}
            disabled={busy || mfaRequired}
            autoComplete="username"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="bambu-region">{t("settingsBambu.region")}</Label>
          <Select
            value={region}
            onValueChange={(value) => setRegion(value as BambuRegion)}
            disabled={busy || mfaRequired}
          >
            <SelectTrigger id="bambu-region">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {REGIONS.map((r) => (
                <SelectItem key={r.value} value={r.value}>
                  {t(r.labelKey)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1.5 sm:col-span-2">
          <Label htmlFor="bambu-password">{t("settingsBambu.password")}</Label>
          <Input
            id="bambu-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={busy || mfaRequired}
            autoComplete="current-password"
          />
        </div>
      </div>

      {!mfaRequired ? (
        <Button type="button" disabled={busy || !account.trim() || !password} onClick={submitLogin}>
          {login.isPending ? t("settingsBambu.connecting") : t("settingsBambu.connect")}
        </Button>
      ) : (
        <div className="space-y-3 border-t pt-4">
          <p className="text-sm text-muted-foreground">
            {t("settingsBambu.mfaSent", { account })}
          </p>
          <div className="flex max-w-xs flex-col gap-1.5">
            <Label htmlFor="bambu-code">{t("settingsBambu.code")}</Label>
            <Input
              id="bambu-code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              disabled={verify.isPending}
              placeholder={t("settingsBambu.codePlaceholder")}
              autoComplete="one-time-code"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" disabled={verify.isPending || !code.trim()} onClick={submitVerify}>
              {verify.isPending ? t("settingsBambu.verifying") : t("settingsBambu.verify")}
            </Button>
            <Button type="button" variant="ghost" disabled={verify.isPending} onClick={startOver}>
              {t("settingsBambu.startOver")}
            </Button>
          </div>
        </div>
      )}

      {login.isError && (
        <p role="alert" className="text-sm text-destructive">
          {login.error instanceof ApiError ? login.error.detail : t("settingsBambu.connectError")}
        </p>
      )}
      {verify.isError && (
        <p role="alert" className="text-sm text-destructive">
          {verify.error instanceof ApiError ? verify.error.detail : t("settingsBambu.verifyError")}
        </p>
      )}
    </div>
  );
}
