/**
 * `/system/finance` (P7 review/billing plan Task 8): a billing on/off
 * switch (manager only), a cost-center table, and the selected center's
 * ledger below it. Members see a read-only view scoped to their own
 * centers (`useCostCenters({ mine: true })`); a manager sees every
 * center.
 */
import { useTranslation } from "react-i18next";
import { useState } from "react";

import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useCostCenters, useFinanceConfig, useSetBilling } from "@/api/finance";
import { CostCenterTable } from "@/components/finance/CostCenterTable";
import { LedgerTable } from "@/components/finance/LedgerTable";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageContainer } from "@/components/ui/page-container";
import { Skeleton } from "@/components/ui/skeleton";
import { SwitchRow } from "@/components/ui/switch-row";

export function FinancePage() {
  const { t } = useTranslation();
  const auth = useAuth();
  const isManager = auth.can("billing:manage");
  const config = useFinanceConfig();
  const setBilling = useSetBilling();
  const centers = useCostCenters({ mine: !isManager });
  const [selectedId, setSelectedId] = useState<number | null>(null);

  return (
    <PageContainer width="default">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">{t("financePage.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("financePage.subtitle")}</p>
      </div>

      {isManager && (
        <Card>
          <CardHeader>
            <CardTitle>{t("financePage.billing")}</CardTitle>
            <CardDescription>
              {t("financePage.billingDesc")}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <SwitchRow
              label={t("financePage.billing")}
              description={config.data?.billing_enabled ? t("financePage.billingOn") : t("financePage.billingOff")}
              checked={!!config.data?.billing_enabled}
              disabled={config.isLoading || setBilling.isPending}
              onCheckedChange={(checked) => setBilling.mutate(checked)}
            />
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>{t("financePage.centers")}</CardTitle>
          <CardDescription>
            {isManager ? t("financePage.centersAll") : t("financePage.centersMine")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {centers.isLoading ? (
            <Skeleton className="h-32 w-full rounded-lg" />
          ) : centers.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {centers.error instanceof ApiError ? centers.error.detail : t("financePage.centersError")}
            </p>
          ) : (
            <CostCenterTable
              centers={centers.data ?? []}
              isManager={isManager}
              selectedId={selectedId}
              onSelect={setSelectedId}
            />
          )}
        </CardContent>
      </Card>

      {selectedId != null && (
        <Card>
          <CardHeader>
            <CardTitle>{t("financePage.ledger")}</CardTitle>
          </CardHeader>
          <CardContent>
            <LedgerTable costCenterId={selectedId} />
          </CardContent>
        </Card>
      )}
    </PageContainer>
  );
}
