import { useTranslation } from "react-i18next";

import { TwoFactorManager } from "@/components/settings/twofa/TwoFactorManager";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/** General tab: self-service two-factor authentication (every user). */
export function TwoFactorCard() {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("twofa.title")}</CardTitle>
        <CardDescription>{t("twofa.desc")}</CardDescription>
      </CardHeader>
      <CardContent>
        <TwoFactorManager />
      </CardContent>
    </Card>
  );
}
