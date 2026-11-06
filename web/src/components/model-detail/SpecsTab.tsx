import { useTranslation } from "react-i18next";
import { meshSpecItems, slicedSpecItems } from "@/components/model-detail/modelSpec";
import { SpecRow } from "@/components/ui/spec-row";
import { FORMAT_LABELS } from "@/lib/formatMeta";
import { humanizeBytes } from "@/lib/format";
import type { ModelDetail } from "@/api/types";

/** Datasheet view of the current revision's per-file specs (Phase 4 studio
 * side panel) -- one `SpecRow` per file with a mesh/CAD or sliced-file
 * shape, skipping files with nothing worth showing (no extracted metadata).
 * Distinct from `FilesTab`'s table: this is read-only spec facts, not file
 * management (rename/delete/download). */
export function SpecsTab({ model }: { model: ModelDetail }) {
  const { t } = useTranslation();
  const files = model.current_revision?.files ?? [];
  const withSpecs = files
    .map((file) => {
      const items = !file.meta
        ? []
        : file.kind === "mesh" || file.kind === "cad"
          ? meshSpecItems(file.meta)
          : file.kind === "sliced"
            ? slicedSpecItems(file.meta)
            : [];
      return { file, items };
    })
    .filter(({ items }) => items.length > 0);

  if (withSpecs.length === 0) {
    return <p className="py-8 text-center text-sm text-muted-foreground">{t("mdSpecs.none")}</p>;
  }

  return (
    <div className="space-y-4">
      {withSpecs.map(({ file, items }) => (
        <div key={file.id} className="space-y-1 border-b border-border pb-3 last:border-0 last:pb-0">
          <div className="flex items-center justify-between gap-2">
            <span className="min-w-0 truncate font-mono text-xs" title={file.rel_path}>
              {file.rel_path}
            </span>
            <span className="shrink-0 text-xs text-muted-foreground">
              {FORMAT_LABELS[file.format]} · {humanizeBytes(file.size)}
            </span>
          </div>
          <SpecRow items={items} />
        </div>
      ))}
    </div>
  );
}
