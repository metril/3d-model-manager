/** Shared by the Print and Slice dialogs: shows which process profile a recipe
 * slices with (it replaces the model file's own process settings) and offers
 * to keep the file's own settings instead. */
import { useTranslation } from "react-i18next";

import type { EmbeddedSettings } from "@/api/slicing";
import type { PrintRecipe } from "@/api/types";
import { Label } from "@/components/ui/label";

/** Layer heights equal within slicer rounding noise. */
function sameLayer(a: number, b: number): boolean {
  return Math.abs(a - b) < 0.005;
}

function layerMismatch(recipe: PrintRecipe, embedded: EmbeddedSettings | null | undefined): boolean {
  const mine = recipe.process_layer_height_mm;
  const theirs = embedded?.layer_height;
  return mine != null && theirs != null && !sameLayer(mine, theirs);
}

/** "Process: 0.28mm Extra Draft @BBL A1M, 0.28 mm" (+ a warning when it differs from the file's). */
export function RecipeProcessLine({
  recipe,
  embedded,
  ignored = false,
}: {
  recipe: PrintRecipe;
  embedded?: EmbeddedSettings | null;
  /** The file's own settings are used instead: this profile is not applied. */
  ignored?: boolean;
}) {
  const { t } = useTranslation();
  if (!recipe.process_profile_name) return null;
  const mismatch = !ignored && layerMismatch(recipe, embedded);
  const layer = recipe.process_layer_height_mm;
  return (
    <span
      data-testid={`recipe-process-${recipe.id}`}
      className={`block text-xs ${mismatch ? "font-medium text-amber-600 dark:text-amber-400" : "text-muted-foreground"} ${ignored ? "line-through" : ""}`}
    >
      {t("recipeProcess.line", { name: recipe.process_profile_name })}
      {layer != null ? ` · ${layer} mm` : ""}
      {mismatch ? ` — ${t("recipeProcess.differs", { layer: embedded?.layer_height })}` : ""}
    </span>
  );
}

/** The "recipe replaces the file's settings" note + the keep-file-settings switch. */
export function FileSettingsOption({
  id,
  embedded,
  checked,
  onChange,
}: {
  id: string;
  embedded: EmbeddedSettings | null | undefined;
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  const { t } = useTranslation();
  // A file with no embedded process settings (STL, bare 3MF) has nothing to keep.
  if (!embedded) return null;
  return (
    <div className="grid gap-1.5 rounded-md border border-border p-2" data-testid="file-settings-option">
      <p className="text-xs text-muted-foreground">{t("recipeProcess.note")}</p>
      <p className="text-xs">
        {t("recipeProcess.fileHas", {
          profile: embedded.print_settings_id ?? t("recipeProcess.unnamed"),
        })}
        {embedded.layer_height != null ? ` · ${embedded.layer_height} mm` : ""}
      </p>
      <div className="flex items-center gap-2">
        <input id={id} type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        <Label htmlFor={id} className="text-xs font-normal">
          {t("recipeProcess.keepFile")}
        </Label>
      </div>
      {checked ? <p className="text-xs text-muted-foreground">{t("recipeProcess.keepFileHint")}</p> : null}
    </div>
  );
}
