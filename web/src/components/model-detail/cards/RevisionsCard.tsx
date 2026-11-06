import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RevisionsTab } from "@/components/model-detail/RevisionsTab";
import type { ModelDetail } from "@/api/types";

/** Right-column card wrapping `RevisionsTab` (R13a), titled "Other versions"
 * to match the GyroidVault wireframe's card label. */
export function RevisionsCard({ model }: { model: ModelDetail }) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("mdCards.otherVersions")}</CardTitle>
      </CardHeader>
      <CardContent>
        <RevisionsTab model={model} />
      </CardContent>
    </Card>
  );
}
