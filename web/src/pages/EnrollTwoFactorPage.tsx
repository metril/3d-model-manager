import { useTranslation } from "react-i18next";

import { useAuth, useLogout } from "@/api/auth";
import { TwoFactorManager } from "@/components/settings/twofa/TwoFactorManager";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/** Blocking gate shown while `me.must_enroll_2fa`: only enrolment and sign-out are offered. */
export function EnrollTwoFactorPage({ onFinished }: { onFinished: () => void }) {
  const { t } = useTranslation();
  const { data } = useAuth();
  const logout = useLogout();
  return (
    <div className="flex min-h-svh items-center justify-center p-4">
      <Card className="w-full max-w-lg">
        <CardHeader>
          <CardTitle>{t("twofa.enroll.title")}</CardTitle>
          <CardDescription>{t("twofa.enroll.desc")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <TwoFactorManager />
          <div className="flex justify-between gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => logout.mutate(undefined, { onSuccess: () => window.location.assign("/login") })}
            >
              {t("shell.logout")}
            </Button>
            {data?.two_factor_enabled ? (
              <Button type="button" onClick={onFinished}>
                {t("twofa.enroll.continue")}
              </Button>
            ) : null}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
