/** Login step 2: enter a TOTP / emailed / backup code. Codes live in component state only. */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useSend2faEmail, useVerify2fa, type TwoFactorMethod, type Verify2faResult } from "@/api/auth";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface Props {
  token: string;
  methods: TwoFactorMethod[];
  onDone: (me: Verify2faResult) => void;
  /** Token is unknown / expired / used up (410): the caller returns to credentials. */
  onExpired: () => void;
  /** Per-user lockout (429 `2fa_locked`): the caller returns to credentials, no retry. */
  onLocked: () => void;
  onBack: () => void;
}

export function TwoFactorStep({ token, methods, onDone, onExpired, onLocked, onBack }: Props) {
  const { t } = useTranslation();
  const primary = methods.filter((m) => m !== "backup");
  const hasBackup = methods.includes("backup");
  const [method, setMethod] = useState<TwoFactorMethod>(primary[0] ?? "backup");
  const [code, setCode] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const verify = useVerify2fa();
  const send = useSend2faEmail();

  // Never keep a code around after leaving the step.
  useEffect(() => () => setCode(""), []);

  function switchTo(m: TwoFactorMethod) {
    setMethod(m);
    setCode("");
    setError(null);
  }

  function submit(value: string) {
    setError(null);
    verify.mutate(
      { pre_auth_token: token, method, code: value.trim() },
      {
        onSuccess: (me) => {
          setCode("");
          verify.reset();
          onDone(me);
        },
        onError: (e) => {
          verify.reset();
          if (e instanceof ApiError && e.status === 410) return onExpired();
          if (e instanceof ApiError && e.code === "2fa_locked") return onLocked();
          setCode("");
          setError(
            e instanceof ApiError && e.status === 401 ? t("twofa.login.invalid") : e instanceof ApiError ? e.detail : t("twofa.login.failed"),
          );
        },
      },
    );
  }

  function sendEmail() {
    setError(null);
    send.mutate(token, {
      onSuccess: () => setSent(true),
      onError: (e) => {
        if (e instanceof ApiError && e.status === 410) return onExpired();
        if (e instanceof ApiError && e.code === "2fa_locked") return onLocked();
        const code = e instanceof ApiError ? e.code : undefined;
        setError(
          code === "rate_limited"
            ? t("twofa.login.rateLimited")
            : code === "smtp_not_configured"
              ? t("twofa.login.noSmtp")
              : t("twofa.login.sendFailed"),
        );
      },
    });
  }

  const autoLen = method === "backup" ? 0 : 6;

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (code.trim()) submit(code);
      }}
    >
      {primary.length > 1 ? (
        <div role="tablist" className="flex gap-1 rounded-md bg-muted p-1">
          {primary.map((m) => (
            <button
              key={m}
              type="button"
              role="tab"
              aria-selected={method === m}
              className={`flex-1 rounded px-2 py-1 text-sm ${method === m ? "bg-background shadow-sm" : "text-muted-foreground"}`}
              onClick={() => switchTo(m)}
            >
              {t(`twofa.method.${m}`)}
            </button>
          ))}
        </div>
      ) : null}
      <p className="text-sm text-muted-foreground">{t(`twofa.login.prompt.${method}`)}</p>
      {method === "email" ? (
        <Button type="button" variant="outline" disabled={send.isPending} onClick={sendEmail}>
          {sent ? t("twofa.login.resend") : t("twofa.login.sendCode")}
        </Button>
      ) : null}
      <div className="flex flex-col gap-1.5">
        <label htmlFor="twofa-code" className="text-sm font-medium">
          {method === "backup" ? t("twofa.login.backupLabel") : t("twofa.login.codeLabel")}
        </label>
        <Input
          id="twofa-code"
          inputMode={method === "backup" ? "text" : "numeric"}
          autoComplete="one-time-code"
          autoFocus
          maxLength={method === "backup" ? 12 : 8}
          value={code}
          onChange={(e) => {
            const v = e.target.value;
            setCode(v);
            if (autoLen && v.trim().length === autoLen && !verify.isPending) submit(v);
          }}
        />
      </div>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      <Button type="submit" disabled={verify.isPending || !code.trim()}>
        {verify.isPending ? t("twofa.login.verifying") : t("twofa.login.verify")}
      </Button>
      {hasBackup && method !== "backup" ? (
        <button type="button" className="text-sm text-muted-foreground underline" onClick={() => switchTo("backup")}>
          {t("twofa.login.useBackup")}
        </button>
      ) : null}
      {method === "backup" && primary.length > 0 ? (
        <button type="button" className="text-sm text-muted-foreground underline" onClick={() => switchTo(primary[0])}>
          {t("twofa.login.useApp")}
        </button>
      ) : null}
      <button type="button" className="text-sm text-muted-foreground underline" onClick={onBack}>
        {t("twofa.login.back")}
      </button>
    </form>
  );
}
