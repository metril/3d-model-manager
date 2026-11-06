import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ProvenanceBlock } from "@/components/model-detail/ProvenanceBlock";
import { StorageLocationBar } from "@/components/model-detail/StorageLocationBar";
import { TagEditor } from "@/components/model-detail/TagEditor";
import type { ModelDetail } from "@/api/types";

/** Left-column card combining tags, import provenance, and storage location
 * (moved out of `ModelHeader` in R13a). The relocate dialog's open state is
 * still owned by the page (`ModelDetailPage`) since it's opened from
 * `ModelHeader`'s "Move / copy…" overflow item, not from this card. */
export function TagsLinksCard({
  model,
  editMode,
  relocateOpen,
  onRelocateOpenChange,
}: {
  model: ModelDetail;
  editMode: boolean;
  relocateOpen: boolean;
  onRelocateOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("mdCards.tagsLinks")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <TagEditor model={model} editMode={editMode} />
        <ProvenanceBlock model={model} />
        <StorageLocationBar model={model} open={relocateOpen} onOpenChange={onRelocateOpenChange} />
      </CardContent>
    </Card>
  );
}
