/** Per-printer Obico failure detection (Q10): enable + action override. */
import { useTranslation } from "react-i18next";
import { ApiError } from "@/api/client";
import type { DetectionAction } from "@/api/detection";
import { usePrinterDetection, useSavePrinterDetection } from "@/api/detection";
import type { PrinterOut } from "@/api/types";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";

export function PrinterDetectionSection({ printer }: { printer: PrinterOut }) {
  const { t } = useTranslation();
  const q = usePrinterDetection(printer.id);
  const save = useSavePrinterDetection(printer.id);
  const id = `printer-${printer.id}-detection`;
  if (!q.data) return null;
  const d = q.data;

  return (
    <fieldset className="flex flex-col gap-3 rounded-md border p-3">
      <legend className="px-1 text-sm font-medium">{t("settingsPrinterDetection.title")}</legend>
      <div className="grid items-end gap-3 sm:grid-cols-2">
        <div className="flex items-center gap-3">
          <Switch
            id={`${id}-enabled`}
            checked={d.enabled}
            disabled={save.isPending}
            onCheckedChange={(enabled) => save.mutate({ enabled, action: d.action })}
          />
          <Label htmlFor={`${id}-enabled`}>{t("settingsPrinterDetection.watch")}</Label>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-action`}>{t("settingsPrinterDetection.action")}</Label>
          <select
            id={`${id}-action`}
            className={selectClass}
            value={d.action ?? ""}
            disabled={save.isPending}
            onChange={(e) =>
              save.mutate({
                enabled: d.enabled,
                action: (e.target.value || null) as DetectionAction | null,
              })
            }
          >
            <option value="">{t("settingsPrinterDetection.default", { action: d.effective_action })}</option>
            <option value="none">{t("settingsPrinterDetection.record")}</option>
            <option value="notify">{t("settingsPrinterDetection.notify")}</option>
            <option value="pause">{t("settingsPrinterDetection.pause")}</option>
          </select>
        </div>
      </div>
      {!d.global_enabled ? (
        <p className="text-xs text-muted-foreground">
          {t("settingsPrinterDetection.offGlobally")}
        </p>
      ) : null}
      {save.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {save.error instanceof ApiError ? save.error.detail : t("settingsPrinterDetection.saveError")}
        </p>
      ) : null}
    </fieldset>
  );
}
