import { useTranslation } from "react-i18next";
import { usePatchModel } from "@/api/library";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { InlineEdit } from "@/components/InlineEdit";
import { sanitizeDescriptionHtml } from "@/lib/richText";
import type { ModelDetail } from "@/api/types";

/** Left-column card for the model description (moved out of `ModelHeader`
 * in R13a) -- edit-gated `InlineEdit` in edit mode, sanitized rich-text
 * render otherwise. Owns its own `usePatchModel` mutation, same posture as
 * `TagEditor`. */
export function DescriptionCard({ model, editMode }: { model: ModelDetail; editMode: boolean }) {
  const { t } = useTranslation();
  const patchModel = usePatchModel(model.slug);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("mdDesc.title")}</CardTitle>
      </CardHeader>
      <CardContent>
        {editMode ? (
          <InlineEdit
            value={model.description ?? ""}
            placeholder={t("mdDesc.placeholder")}
            aria-label={t("mdDesc.aria")}
            multiline
            onSave={(description) => patchModel.mutate({ description: description || null })}
            displayClassName="text-sm text-muted-foreground"
          />
        ) : model.description ? (
          <div
            className="prose-compact text-sm text-muted-foreground"
            dangerouslySetInnerHTML={{ __html: sanitizeDescriptionHtml(model.description) }}
          />
        ) : (
          <p className="text-sm text-muted-foreground">{t("mdDesc.empty")}</p>
        )}
      </CardContent>
    </Card>
  );
}
