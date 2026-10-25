import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** Ask for one code / password, run `onSubmit`, show API errors inline. The value is cleared on close. */
export function CodePromptDialog({
  open,
  onOpenChange,
  title,
  description,
  label,
  secret,
  confirmLabel,
  destructive,
  onSubmit,
  extra,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  label: string;
  /** Mask the input (code_or_password flows). */
  secret?: boolean;
  confirmLabel: string;
  destructive?: boolean;
  onSubmit: (value: string) => Promise<unknown>;
  extra?: ReactNode;
}) {
  const { t } = useTranslation();
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) {
      setValue("");
      setError(null);
    }
  }, [open]);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await onSubmit(value.trim());
      onOpenChange(false);
    } catch (e) {
      if (e instanceof ApiError && e.code === "2fa_locked") {
        toast.error(t("twofa.login.locked"));
        onOpenChange(false);
        return;
      }
      setError(e instanceof ApiError ? (e.code === "invalid_code" ? t("twofa.login.invalid") : e.detail) : t("twofa.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description ? <DialogDescription>{description}</DialogDescription> : null}
        </DialogHeader>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (value.trim()) void submit();
          }}
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="twofa-prompt">{label}</Label>
            <Input
              id="twofa-prompt"
              type={secret ? "password" : "text"}
              autoComplete="one-time-code"
              inputMode={secret ? undefined : "numeric"}
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          </div>
          {extra}
          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
          <DialogFooter showCloseButton>
            <Button type="submit" variant={destructive ? "destructive" : "default"} disabled={busy || !value.trim()}>
              {confirmLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
