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
import { useAppSettings, useUpdateAppSettings } from "@/api/appSettings";
import { ApiError } from "@/api/client";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { SwitchRow } from "@/components/ui/switch-row";

export function PrinterEnabledCard() {
  const settings = useAppSettings();
  const update = useUpdateAppSettings();

  if (settings.isLoading) return <Skeleton className="h-32 w-full rounded-xl" />;

  if (settings.isError || !settings.data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Printer integration</CardTitle>
          <CardDescription>
            {settings.error instanceof ApiError ? settings.error.detail : "Couldn't load this setting."}
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
        <CardTitle>Printer integration</CardTitle>
        <CardDescription>
          Connect a Bambu printer over your LAN to send sliced files and see live status. Changes
          take effect immediately — no restart.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <SwitchRow id="printer-enabled" label="Enable printer integration" checked={current.printer_enabled} disabled={update.isPending} onCheckedChange={toggle} />
        {update.isError && (
          <p role="alert" className="text-sm text-destructive">
            {update.error instanceof ApiError ? update.error.detail : "Could not update this setting."}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
