import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { FORMAT_LABELS } from "@/lib/formatMeta";
import type { FileOut } from "@/api/types";

/** Thin strip under the viewer stage (R13a GyroidVault re-chrome): one format
 * badge per distinct checked-part format + the checked filenames + a fixed
 * orbit/zoom/fit hint. Purely informational -- no controls, so it never
 * needs its own hotkey guards. */
export function ViewerFooterStrip({
  files,
  checkedIds,
}: {
  files: FileOut[];
  checkedIds: ReadonlySet<number>;
}) {
  const { t } = useTranslation();
  const checked = files.filter((file) => checkedIds.has(file.id));
  const formats = [...new Set(checked.map((file) => file.format))];
  const names = checked.map((file) => file.rel_path).join(", ");

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2 px-1 text-xs text-muted-foreground">
      {formats.map((format) => (
        <Badge key={format} variant="outline">
          {FORMAT_LABELS[format]}
        </Badge>
      ))}
      {names && (
        <span className="min-w-0 flex-1 truncate" title={names}>
          {names}
        </span>
      )}
      <span className="shrink-0 max-sm:hidden [@media(hover:none)]:hidden">{t("viewer.dragHint")}</span>
    </div>
  );
}
