import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useSecuritySettings, useUpdateSecuritySettings } from "@/api/security";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { SwitchRow } from "@/components/ui/switch-row";

/** Admin (Access tab): force every user to enrol in two-factor authentication. */
export function SecurityCard() {
  const { t } = useTranslation();
  const q = useSecuritySettings();
  const update = useUpdateSecuritySettings();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("twofa.security.title")}</CardTitle>
        <CardDescription>{t("twofa.security.desc")}</CardDescription>
      </CardHeader>
      <CardContent>
        {q.isLoading ? (
          <Skeleton className="h-6 w-48" />
        ) : (
          <SwitchRow
            label={t("twofa.security.require")}
            checked={q.data?.require_2fa ?? false}
            disabled={update.isPending || !q.data}
            onCheckedChange={(require_2fa) =>
              update.mutate(
                { require_2fa },
                { onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("twofa.error")) },
              )
            }
          />
        )}
      </CardContent>
    </Card>
  );
}
