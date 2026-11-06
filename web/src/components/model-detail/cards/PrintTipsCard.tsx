import { useTranslation } from "react-i18next";
import { usePatchModel } from "@/api/library";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { InlineEdit } from "@/components/InlineEdit";
import type { ModelDetail } from "@/api/types";

/** Right-column card for freeform print tips (`models.print_tips`, R13c).
 * Same posture as `DescriptionCard` -- owns its own `usePatchModel`
 * mutation and always shows the `InlineEdit` affordance (no page-level
 * edit-mode gate). Empty/whitespace-only saves clear the field to `null`. */
export function PrintTipsCard({ model }: { model: ModelDetail }) {
  const { t } = useTranslation();
  const patchModel = usePatchModel(model.slug);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("mdTips.title")}</CardTitle>
      </CardHeader>
      <CardContent>
        <InlineEdit
          value={model.print_tips ?? ""}
          placeholder={t("mdTips.placeholder")}
          aria-label={t("mdTips.aria")}
          multiline
          onSave={(print_tips) => patchModel.mutate({ print_tips: print_tips || null })}
          displayClassName="text-sm text-muted-foreground"
        />
      </CardContent>
    </Card>
  );
}
