import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { NotesTab } from "@/components/model-detail/NotesTab";
import type { ModelDetail } from "@/api/types";

/** Right-column card wrapping `NotesTab` (R13a). */
export function NotesCard({ model }: { model: ModelDetail }) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("mdCards.notes")}</CardTitle>
      </CardHeader>
      <CardContent>
        <NotesTab model={model} />
      </CardContent>
    </Card>
  );
}
