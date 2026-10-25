/** Per-spool supplier links: supplier, article no., quoted price/kg, one purchase source. */
import { Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useSuppliers } from "@/api/spoolAdmin";
import type { SpoolSupplierLink } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export function SupplierListEditor({
  value,
  onChange,
  disabled,
}: {
  value: SpoolSupplierLink[];
  onChange: (next: SpoolSupplierLink[]) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const suppliers = useSuppliers();
  const options = suppliers.data ?? [];
  const usedBy = (i: number) => new Set(value.filter((_, j) => j !== i).map((r) => r.supplier_id));

  const patch = (i: number, p: Partial<SpoolSupplierLink>) => onChange(value.map((r, j) => (j === i ? { ...r, ...p } : r)));
  const setSource = (i: number) => onChange(value.map((r, j) => ({ ...r, is_purchase_source: j === i })));
  const add = () => {
    const used = new Set(value.map((r) => r.supplier_id));
    const first = options.find((s) => !used.has(s.id));
    if (!first) return;
    onChange([
      ...value,
      { supplier_id: first.id, article_number: null, quoted_price_per_kg: null, is_purchase_source: value.length === 0 },
    ]);
  };

  return (
    <div className="flex flex-col gap-2">
      {value.map((row, i) => (
        <div key={row.supplier_id} className="grid grid-cols-[1fr_7rem_6rem_auto_auto] items-center gap-2">
          <Select
            aria-label={t("spoolAdmin.sup.supplier")}
            value={String(row.supplier_id)}
            onValueChange={(v) => patch(i, { supplier_id: Number(v) })}
            disabled={disabled}
          >
            <SelectTrigger aria-label={t("spoolAdmin.sup.supplier")} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {options.filter((s) => !usedBy(i).has(s.id)).map((s) => (
                <SelectItem key={s.id} value={String(s.id)}>
                  {s.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            aria-label={t("spoolAdmin.sup.article")}
            placeholder={t("spoolAdmin.sup.article")}
            value={row.article_number ?? ""}
            disabled={disabled}
            onChange={(e) => patch(i, { article_number: e.target.value || null })}
          />
          <Input
            type="number"
            step="0.01"
            aria-label={t("spoolAdmin.sup.price")}
            placeholder={t("spoolAdmin.sup.pricePlaceholder")}
            value={row.quoted_price_per_kg ?? ""}
            disabled={disabled}
            onChange={(e) => patch(i, { quoted_price_per_kg: e.target.value === "" ? null : Number(e.target.value) })}
          />
          <label className="flex items-center gap-1 text-xs">
            <input
              type="radio"
              name="purchase-source"
              aria-label={t("spoolAdmin.sup.source")}
              checked={row.is_purchase_source}
              disabled={disabled}
              onChange={() => setSource(i)}
            />
            {t("spoolAdmin.sup.sourceShort")}
          </label>
          <Button
            type="button"
            size="icon-sm"
            variant="ghost"
            aria-label={t("spoolAdmin.sup.remove")}
            disabled={disabled}
            onClick={() => onChange(value.filter((_, j) => j !== i))}
          >
            <Trash2 className="size-4" />
          </Button>
        </div>
      ))}
      <div>
        <Button type="button" size="sm" variant="outline" disabled={disabled || options.length <= value.length} onClick={add}>
          {t("spoolAdmin.sup.add")}
        </Button>
        {options.length === 0 && !suppliers.isLoading ? (
          <span className="ml-2 text-xs text-muted-foreground">{t("spoolAdmin.sup.noneDefined")}</span>
        ) : null}
      </div>
    </div>
  );
}
