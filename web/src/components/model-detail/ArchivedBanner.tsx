import { useTranslation } from "react-i18next";
import { useArchiveModel } from "@/api/library";
import { Button } from "@/components/ui/button";
import type { ModelDetail } from "@/api/types";

/** Archived-model notice (feat/import-fidelity T4) shown above the tabs on
 * the model detail page. Unarchive is always visible here -- not gated
 * behind `ModelHeader`'s edit toggle, same posture as the favorite star and
 * the Re-download action -- since a model that's hidden from the default
 * library view needs an obvious, low-friction way back out. Renders nothing
 * for a non-archived model. */
export function ArchivedBanner({ model }: { model: ModelDetail }) {
  const archiveModel = useArchiveModel(model.slug);
  const { t } = useTranslation();

  if (!model.is_archived) return null;

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-muted/50 px-4 py-2.5 text-sm text-muted-foreground">
      <span>{t("mdArchived.banner")}</span>
      <Button type="button" variant="outline" size="sm" onClick={() => archiveModel.mutate(false)}>
        {t("mdArchived.unarchive")}
      </Button>
    </div>
  );
}
