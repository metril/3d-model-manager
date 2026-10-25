/** Status rows + setup/disable/regenerate actions; shared by `TwoFactorCard` and the enrolment gate. */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  useEmailOtpConfirm,
  useEmailOtpDisable,
  useEmailOtpEnable,
  useEmailOtpRequest,
  useRegenerateBackup,
  useTotpDisable,
  useTwoFactorStatus,
} from "@/api/auth";
import { ApiError } from "@/api/client";
import { BackupCodes } from "@/components/settings/twofa/BackupCodes";
import { CodePromptDialog } from "@/components/settings/twofa/CodePromptDialog";
import { TotpSetupDialog } from "@/components/settings/twofa/TotpSetupDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";

type Prompt = "totp-disable" | "email-confirm" | "email-disable" | "regen" | null;

export function TwoFactorManager() {
  const { t } = useTranslation();
  const status = useTwoFactorStatus();
  const [totpOpen, setTotpOpen] = useState(false);
  const [prompt, setPrompt] = useState<Prompt>(null);
  const [newCodes, setNewCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const totpDisable = useTotpDisable();
  const regen = useRegenerateBackup();
  const emailEnable = useEmailOtpEnable();
  const emailConfirm = useEmailOtpConfirm();
  const emailDisable = useEmailOtpDisable();
  const emailRequest = useEmailOtpRequest();

  if (status.isLoading) return <Skeleton className="h-24 w-full rounded-lg" />;
  if (status.isError || !status.data) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {status.error instanceof ApiError ? status.error.detail : t("twofa.loadError")}
      </p>
    );
  }
  const s = status.data;

  function startEmail() {
    setError(null);
    emailEnable.mutate(undefined, {
      onSuccess: () => setPrompt("email-confirm"),
      onError: (e) => setError(e instanceof ApiError ? e.detail : t("twofa.error")),
    });
  }

  const row = (title: string, on: boolean, detail: string, actions: React.ReactNode) => (
    <div className="flex flex-wrap items-center gap-2 py-2">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-medium">{title}</span>
          <Badge variant={on ? "secondary" : "outline"}>{on ? t("twofa.on") : t("twofa.off")}</Badge>
        </div>
        <p className="text-xs text-muted-foreground">{detail}</p>
      </div>
      <div className="flex gap-2">{actions}</div>
    </div>
  );

  return (
    <div className="divide-y">
      {row(
        t("twofa.method.totp"),
        s.totp_enabled,
        t("twofa.totp.rowDesc"),
        s.totp_enabled ? (
          <Button type="button" size="sm" variant="outline" onClick={() => setPrompt("totp-disable")}>
            {t("twofa.disable")}
          </Button>
        ) : (
          <Button type="button" size="sm" onClick={() => setTotpOpen(true)}>
            {t("twofa.setUp")}
          </Button>
        ),
      )}
      {row(
        t("twofa.method.email"),
        s.email_otp_enabled,
        s.email_otp_enabled || s.email_available
          ? t("twofa.email.rowDesc")
          : s.smtp_configured
            ? t("twofa.email.noAddress")
            : t("twofa.email.noSmtp"),
        s.email_otp_enabled ? (
          <Button type="button" size="sm" variant="outline" onClick={() => setPrompt("email-disable")}>
            {t("twofa.disable")}
          </Button>
        ) : (
          <Button type="button" size="sm" disabled={!s.email_available || emailEnable.isPending} onClick={startEmail}>
            {t("twofa.setUp")}
          </Button>
        ),
      )}
      {row(
        t("twofa.backup.title"),
        s.backup_codes_remaining > 0,
        t("twofa.backup.remaining", { count: s.backup_codes_remaining }),
        s.totp_enabled ? (
          <Button type="button" size="sm" variant="outline" onClick={() => setPrompt("regen")}>
            {t("twofa.backup.regenerate")}
          </Button>
        ) : null,
      )}
      {error ? (
        <p role="alert" className="pt-2 text-sm text-destructive">
          {error}
        </p>
      ) : null}

      <TotpSetupDialog open={totpOpen} onOpenChange={setTotpOpen} />

      <CodePromptDialog
        open={prompt === "totp-disable"}
        onOpenChange={(o) => !o && setPrompt(null)}
        title={t("twofa.totp.disableTitle")}
        description={t("twofa.disableDesc")}
        label={t("twofa.codeOrPassword")}
        secret
        destructive
        confirmLabel={t("twofa.disable")}
        onSubmit={(v) => totpDisable.mutateAsync(v)}
      />
      <CodePromptDialog
        open={prompt === "regen"}
        onOpenChange={(o) => !o && setPrompt(null)}
        title={t("twofa.backup.regenTitle")}
        description={t("twofa.backup.regenDesc")}
        label={t("twofa.totp.codeLabel")}
        confirmLabel={t("twofa.backup.regenerate")}
        onSubmit={async (v) => {
          setNewCodes((await regen.mutateAsync(v)).backup_codes);
          regen.reset();
        }}
      />
      <CodePromptDialog
        open={prompt === "email-confirm"}
        onOpenChange={(o) => !o && setPrompt(null)}
        title={t("twofa.email.confirmTitle")}
        description={t("twofa.email.confirmDesc")}
        label={t("twofa.email.codeLabel")}
        confirmLabel={t("twofa.email.enable")}
        onSubmit={(v) => emailConfirm.mutateAsync(v)}
      />
      <CodePromptDialog
        open={prompt === "email-disable"}
        onOpenChange={(o) => !o && setPrompt(null)}
        title={t("twofa.email.disableTitle")}
        description={t("twofa.disableDesc")}
        label={t("twofa.codeOrPassword")}
        secret
        destructive
        confirmLabel={t("twofa.disable")}
        onSubmit={(v) => emailDisable.mutateAsync(v)}
        extra={
          <Button type="button" variant="outline" size="sm" disabled={emailRequest.isPending} onClick={() => emailRequest.mutate()}>
            {emailRequest.isSuccess ? t("twofa.email.sent") : t("twofa.email.sendMe")}
          </Button>
        }
      />
      <Dialog open={newCodes !== null} onOpenChange={(o) => {
          if (!o) {
            setNewCodes(null);
            regen.reset();
          }
        }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t("twofa.totp.codesTitle")}</DialogTitle>
            <DialogDescription>{t("twofa.backup.regenDone")}</DialogDescription>
          </DialogHeader>
          {newCodes ? <BackupCodes codes={newCodes} /> : null}
          <DialogFooter>
            <Button type="button" onClick={() => {
                setNewCodes(null);
                regen.reset();
              }}>
              {t("twofa.totp.saved")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
