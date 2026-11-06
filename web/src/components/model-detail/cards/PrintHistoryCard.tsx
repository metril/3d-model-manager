import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PrintsTab } from "@/components/model-detail/PrintsTab";
import type { ModelDetail } from "@/api/types";

/** Right-column card wrapping `PrintsTab` (R13a) -- `PrintsTab` owns its own
 * "Log a print" toggle (a header-style action inside its own body) so
 * history shows first instead of an always-expanded composer form. */
export function PrintHistoryCard({ model }: { model: ModelDetail }) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("mdCards.printHistory")}</CardTitle>
      </CardHeader>
      <CardContent>
        <PrintsTab model={model} />
      </CardContent>
    </Card>
  );
}
