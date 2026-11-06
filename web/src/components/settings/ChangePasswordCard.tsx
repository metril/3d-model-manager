/**
 * Change-password card (Round 10 T4/T5): General tab. `POST /auth/password`
 * rotates the admin's password hash and signs out every OTHER session for
 * this user server-side -- the tab making the request stays signed in, so no
 * redirect or re-login is needed here on success.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useChangePassword } from "@/api/auth";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function ChangePasswordCard() {
  const { t } = useTranslation();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [mismatch, setMismatch] = useState(false);
  const changePassword = useChangePassword();

  function submit() {
    changePassword.reset();
    if (next !== confirm) {
      setMismatch(true);
      return;
    }
    setMismatch(false);
    changePassword.mutate(
      { current_password: current, new_password: next },
      {
        onSuccess: () => {
          setCurrent("");
          setNext("");
          setConfirm("");
        },
      },
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settingsPassword.title")}</CardTitle>
        <CardDescription>
          {t("settingsPassword.desc")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="current-password">{t("settingsPassword.current")}</Label>
          <Input
            id="current-password"
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="new-password">{t("settingsPassword.new")}</Label>
          <Input
            id="new-password"
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="confirm-password">{t("settingsPassword.confirm")}</Label>
          <Input
            id="confirm-password"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </div>
        <Button type="button" disabled={changePassword.isPending} onClick={submit}>
          {changePassword.isPending ? t("settingsPassword.updating") : t("settingsPassword.submit")}
        </Button>
        {mismatch && (
          <p role="alert" className="text-sm text-destructive">
            {t("settingsPassword.mismatch")}
          </p>
        )}
        {!mismatch && changePassword.isError && (
          <p role="alert" className="text-sm text-destructive">
            {changePassword.error instanceof ApiError ? changePassword.error.detail : t("settingsPassword.error")}
          </p>
        )}
        {!mismatch && changePassword.isSuccess && (
          <p className="text-sm text-success">{t("settingsPassword.success")}</p>
        )}
      </CardContent>
    </Card>
  );
}
