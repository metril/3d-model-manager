import { useCategories } from "@/api/categories";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { tagColorClass } from "@/lib/tagColors";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";

const NONE_VALUE = "__none__";

/** Single-select category assignment (R13b) -- a model has at most one
 * category, unlike its many-per-model tags, so this is a plain `<Select>`
 * rather than a chip editor. `"None"` clears it (`onChange(null)`). Used from
 * `ModelHeader`'s edit-mode badge row and the New-model dialog. */
export function CategoryPicker({
  value,
  onChange,
  disabled,
}: {
  value: number | null;
  onChange: (categoryId: number | null) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const categoriesQuery = useCategories();
  const categories = categoriesQuery.data ?? [];

  return (
    <Select
      value={value !== null ? String(value) : NONE_VALUE}
      onValueChange={(next) => onChange(next === NONE_VALUE ? null : Number(next))}
      disabled={disabled}
    >
      <SelectTrigger aria-label={t("newModel.category")}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NONE_VALUE}>{t("newModel.none")}</SelectItem>
        {categories.map((category) => (
          <SelectItem key={category.id} value={String(category.id)}>
            <span className="flex items-center gap-1.5">
              <span
                aria-hidden="true"
                className={cn("size-2 shrink-0 rounded-full", tagColorClass(category.color) ?? "bg-muted-foreground")}
              />
              {category.name}
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
