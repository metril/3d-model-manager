import { CheckIcon, CopyIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useTotpEnable, useTotpSetup, type TotpSetup } from "@/api/auth";
import { ApiError } from "@/api/client";
import { BackupCodes } from "@/components/settings/twofa/BackupCodes";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { QrCode } from "@/components/ui/qr-code";

/** Authenticator-app setup: QR + manual secret -> verify a code -> one-time backup codes. */
export function TotpSetupDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation();
  const setup = useTotpSetup();
  const enable = useTotpEnable();
  const [data, setData] = useState<TotpSetup | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  function start() {
    setError(null);
    setup.mutate(undefined, {
      onSuccess: (d) => {
        setData(d);
        setup.reset();
      },
      onError: (e) => setError(e instanceof ApiError ? e.detail : t("twofa.error")),
    });
  }

  useEffect(() => {
    if (open) start();
    else {
      // Secrets, setup token, code and backup codes must not outlive the dialog.
      setData(null);
      setCode("");
      setCodes(null);
      setError(null);
      setup.reset();
      enable.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function submit() {
    if (!data) return;
    setError(null);
    enable.mutate(
      { setup_token: data.setup_token, code: code.trim() },
      {
        onSuccess: (r) => {
          setCode("");
          setCodes(r.backup_codes);
          setData(null); // secret + setup token no longer needed
          enable.reset(); // backup codes live in component state only
        },
        onError: (e) => {
          if (e instanceof ApiError && e.status === 410) {
            setError(t("twofa.totp.expired"));
            setData(null);
          } else setError(e instanceof ApiError ? (e.code === "invalid_code" ? t("twofa.login.invalid") : e.detail) : t("twofa.error"));
        },
      },
    );
  }

  function copySecret() {
    if (!data) return;
    void navigator.clipboard.writeText(data.secret).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <Dialog open={open} onOpenChange={(o) => (codes && !o ? onOpenChange(false) : onOpenChange(o))}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{codes ? t("twofa.totp.codesTitle") : t("twofa.totp.setupTitle")}</DialogTitle>
          <DialogDescription>{codes ? t("twofa.totp.codesDesc") : t("twofa.totp.setupDesc")}</DialogDescription>
        </DialogHeader>
        {codes ? (
          <>
            <BackupCodes codes={codes} />
            <DialogFooter>
              <Button type="button" onClick={() => onOpenChange(false)}>
                {t("twofa.totp.saved")}
              </Button>
            </DialogFooter>
          </>
        ) : (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (code.trim()) submit();
            }}
          >
            {data ? (
              <>
                <div className="flex justify-center">
                  <QrCode value={data.otpauth_url} label={t("twofa.totp.qr")} />
                </div>
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">{t("twofa.totp.manual")}</p>
                  <div className="flex items-center gap-2">
                    <code className="break-all text-sm" data-testid="totp-secret">
                      {data.secret}
                    </code>
                    <Button type="button" size="icon-sm" variant="ghost" aria-label={t("twofa.totp.copySecret")} onClick={copySecret}>
                      {copied ? <CheckIcon /> : <CopyIcon />}
                    </Button>
                  </div>
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="totp-code">{t("twofa.totp.codeLabel")}</Label>
                  <Input
                    id="totp-code"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={8}
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                  />
                </div>
              </>
            ) : setup.isPending ? (
              <p className="text-sm text-muted-foreground">{t("twofa.loading")}</p>
            ) : null}
            {error ? (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            ) : null}
            <DialogFooter showCloseButton>
              {data ? (
                <Button type="submit" disabled={enable.isPending || !code.trim()}>
                  {t("twofa.totp.enable")}
                </Button>
              ) : !setup.isPending ? (
                <Button type="button" onClick={start}>
                  {t("twofa.totp.restart")}
                </Button>
              ) : null}
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
