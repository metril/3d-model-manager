import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { TagOut } from "@/api/types";
import { TagPicker } from "@/components/tags/TagPicker";
import { Button } from "@/components/ui/button";
import { ColorField } from "@/components/ui/color-field";

/** Action bar shown while tags are selected: merge target picker, delete, recolor, clear. */
export function TagBulkBar({
  count,
  all,
  busy,
  onPickMergeTarget,
  onDelete,
  onRecolor,
  onClear,
}: {
  count: number;
  all: TagOut[];
  busy: boolean;
  onPickMergeTarget: (target: TagOut) => void;
  onDelete: () => void;
  onRecolor: (hex: string) => void;
  onClear: () => void;
}) {
  const { t } = useTranslation();
  const [color, setColor] = useState("#64748b");

  return (
    <div
      role="toolbar"
      aria-label={t("settings.tags.bulk.selected", { count })}
      className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/50 px-3 py-2"
    >
      <span className="text-sm font-medium">{t("settings.tags.bulk.selected", { count })}</span>
      <TagPicker
        label={t("settings.tags.bulk.mergeInto")}
        options={all}
        onPick={(tag) => onPickMergeTarget(all.find((o) => o.id === tag.id) ?? tag)}
        disabled={busy}
        trigger={
          <Button type="button" size="sm" variant="outline" disabled={busy || all.length < 2}>
            {t("settings.tags.bulk.merge")}
          </Button>
        }
      />
      <div className="w-32">
        <ColorField
          label={t("settings.tags.bulk.recolorLabel")}
          value={color}
          onChange={setColor}
          clearable={false}
          disabled={busy}
        />
      </div>
      <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => onRecolor(color)}>
        {t("settings.tags.bulk.recolor")}
      </Button>
      <Button type="button" size="sm" variant="destructive" disabled={busy} onClick={onDelete}>
        {t("settings.tags.bulk.delete")}
      </Button>
      <Button type="button" size="sm" variant="ghost" className="ml-auto" onClick={onClear}>
        {t("settings.tags.bulk.clear")}
      </Button>
    </div>
  );
}
