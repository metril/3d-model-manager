/** Settings -> Integrations -> default MakerWorld print-profile filter for imports. */
import { useAppSettings, useUpdateAppSettings } from "@/api/appSettings";
import { ApiError } from "@/api/client";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { SwitchRow } from "@/components/ui/switch-row";

export function MakerWorldProfilesCard() {
  const settings = useAppSettings();
  const update = useUpdateAppSettings();
  if (settings.isLoading) return <Skeleton className="h-32 w-full rounded-xl" />;
  if (!settings.data) return null;
  const initial = settings.data;
  return (
    <Card>
      <CardHeader>
        <CardTitle>MakerWorld profiles</CardTitle>
        <CardDescription>
          A MakerWorld model can ship many print profiles. Import only those compatible with your
          enabled printers, or keep them all. You can override this per import on the Add page.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <SwitchRow
          id="makerworld-owned-profiles"
          label="Only import profiles for my printers"
          description="If no profile matches any of your printers, every profile is kept."
          checked={initial.makerworld_profiles === "owned"}
          disabled={update.isPending}
          onCheckedChange={(on) =>
            update.mutate({ ...initial, makerworld_profiles: on ? "owned" : "all" })
          }
        />
        {update.isError && (
          <p role="alert" className="text-sm text-destructive">
            {update.error instanceof ApiError ? update.error.detail : "Could not save this setting."}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
