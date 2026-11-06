/**
 * Printer integration on/off toggle (Round 10 T5): renders ABOVE
 * `PrinterSetupCard` on the Printer tab and is ALWAYS visible (unlike
 * `PrinterSetupCard`, which hides its whole form while the flag is off) --
 * this card is the only way to turn printer integration on in the first
 * place. Flips `printer_enabled` via `PUT /settings/app`; the mutation's
 * `onSuccess` (see `api/appSettings.ts`) invalidates the `["features"]`
 * query, so `PrinterSetupCard`/`PrinterPage`/`SendToPrinterButton`/
 * `QueuePage`/`SlicerIntegrationCard`/AppShell's nav all react immediately --
 * no restart, no reload.
 */
import { useTranslation } from "react-i18next";
import { useAppSettings, useUpdateAppSettings } from "@/api/appSettings";
import { ApiError } from "@/api/client";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { SwitchRow } from "@/components/ui/switch-row";

export function PrinterEnabledCard() {
  const { t } = useTranslation();
  const settings = useAppSettings();
  const update = useUpdateAppSettings();

  if (settings.isLoading) return <Skeleton className="h-32 w-full rounded-xl" />;

  if (settings.isError || !settings.data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t("settingsPrinterEnabled.title")}</CardTitle>
          <CardDescription>
            {settings.error instanceof ApiError ? settings.error.detail : t("settingsPrinterEnabled.loadError")}
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const current = settings.data;

  function toggle(checked: boolean) {
    update.mutate({ ...current, printer_enabled: checked });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settingsPrinterEnabled.title")}</CardTitle>
        <CardDescription>
          {t("settingsPrinterEnabled.description")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <SwitchRow id="printer-enabled" label={t("settingsPrinterEnabled.enable")} checked={current.printer_enabled} disabled={update.isPending} onCheckedChange={toggle} />
        {update.isError && (
          <p role="alert" className="text-sm text-destructive">
            {update.error instanceof ApiError ? update.error.detail : t("settingsPrinterEnabled.updateError")}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
