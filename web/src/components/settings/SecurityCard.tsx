import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useSecuritySettings, useUpdateSecuritySettings } from "@/api/security";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";

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
          <label className="flex items-center gap-2 text-sm">
            <Switch
              checked={q.data?.require_2fa ?? false}
              disabled={update.isPending || !q.data}
              aria-label={t("twofa.security.require")}
              onCheckedChange={(require_2fa) =>
                update.mutate(
                  { require_2fa },
                  { onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("twofa.error")) },
                )
              }
            />
            {t("twofa.security.require")}
          </label>
        )}
      </CardContent>
    </Card>
  );
}
