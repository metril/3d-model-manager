/**
 * Key/value custom-fields editor (R13c) -- backs `models.metadata`, a
 * freeform string-to-string map with no fixed schema. Lives on `SpecsCard`.
 *
 * Local rows are seeded from `model.metadata` once (on mount / when the
 * model itself changes via `model.id`), not on every render -- reseeding on
 * every `model.metadata` change would clobber an in-progress edit each time
 * an unrelated field patch refetches the detail query. Tradeoff: if another
 * client edits metadata concurrently while this one has the editor open,
 * that external change won't appear until the row set reseeds (next mount).
 * Accepted as a rare edge case, same spirit as `InlineEdit`'s "draft doesn't
 * track external changes while open."
 *
 * Persistence is save-on-blur: each field's `onBlur` PATCHes the *whole*
 * metadata object (not a diff) since the API takes the full map. Deleting a
 * row patches immediately rather than waiting for a blur elsewhere.
 */
import { useTranslation } from "react-i18next";
import { useEffect, useRef, useState } from "react";
import { PlusIcon, Trash2Icon } from "lucide-react";
import { toast } from "sonner";

import { usePatchModel } from "@/api/library";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { ModelDetail } from "@/api/types";

const MAX_ROWS = 50;

interface Row {
  id: number;
  key: string;
  value: string;
}

function rowsFromMetadata(metadata: Record<string, string> | null): Row[] {
  let nextId = 0;
  return Object.entries(metadata ?? {}).map(([key, value]) => ({ id: nextId++, key, value }));
}

/** Builds the metadata object to send: trims keys, drops rows with an empty
 * key, last-write-wins on duplicate (trimmed) keys. */
function objectFromRows(rows: Row[]): Record<string, string> {
  const result: Record<string, string> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (!key) continue;
    result[key] = row.value;
  }
  return result;
}

export function MetadataEditor({ model }: { model: ModelDetail }) {
  const patchModel = usePatchModel(model.slug);
  const { t } = useTranslation();
  const [rows, setRows] = useState<Row[]>(() => rowsFromMetadata(model.metadata));
  // Per-row id counter, stable across renders -- avoids index-based keys so
  // deleting a row in the middle doesn't shift focus onto the row that
  // slides up into its place.
  const nextRowId = useRef(rows.length);
  const seededModelId = useRef(model.id);

  useEffect(() => {
    if (seededModelId.current === model.id) return;
    seededModelId.current = model.id;
    const seeded = rowsFromMetadata(model.metadata);
    nextRowId.current = seeded.length;
    setRows(seeded);
  }, [model.id, model.metadata]);

  function patch(nextRows: Row[]) {
    patchModel.mutate(
      { metadata: objectFromRows(nextRows) },
      { onError: () => toast.error(t("mdMeta.saveFailed")) },
    );
  }

  function updateRow(id: number, field: "key" | "value", value: string) {
    setRows((prev) => prev.map((row) => (row.id === id ? { ...row, [field]: value } : row)));
  }

  function addRow() {
    setRows((prev) => [...prev, { id: nextRowId.current++, key: "", value: "" }]);
  }

  function removeRow(id: number) {
    setRows((prev) => {
      const next = prev.filter((row) => row.id !== id);
      patch(next);
      return next;
    });
  }

  const atCap = rows.length >= MAX_ROWS;

  return (
    <div className="space-y-3" data-testid="metadata-editor">
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("mdMeta.empty")}</p>
      ) : (
        <div className="space-y-2">
          {rows.map((row) => (
            <div key={row.id} className="flex items-end gap-2">
              <div className="flex flex-1 flex-col gap-1.5">
                <Label htmlFor={`metadata-key-${row.id}`}>{t("mdMeta.field")}</Label>
                <Input
                  id={`metadata-key-${row.id}`}
                  value={row.key}
                  placeholder={t("mdMeta.fieldName")}
                  maxLength={64}
                  onChange={(event) => updateRow(row.id, "key", event.target.value)}
                  onBlur={() => patch(rows)}
                />
              </div>
              <div className="flex flex-1 flex-col gap-1.5">
                <Label htmlFor={`metadata-value-${row.id}`}>{t("mdMeta.value")}</Label>
                <Input
                  id={`metadata-value-${row.id}`}
                  value={row.value}
                  placeholder={t("mdMeta.value")}
                  maxLength={2000}
                  onChange={(event) => updateRow(row.id, "value", event.target.value)}
                  onBlur={() => patch(rows)}
                />
              </div>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label={t("mdMeta.removeAria", { name: row.key || t("mdMeta.fieldFallback") })}
                onClick={() => removeRow(row.id)}
              >
                <Trash2Icon />
              </Button>
            </div>
          ))}
        </div>
      )}
      <Button type="button" variant="outline" size="sm" onClick={addRow} disabled={atCap}>
        <PlusIcon />
        {t("mdMeta.addField")}
      </Button>
      {atCap && <p className="text-xs text-muted-foreground">{t("mdMeta.cap")}</p>}
    </div>
  );
}
