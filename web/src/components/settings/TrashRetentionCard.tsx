/** Settings -> Storage -> how long trashed models are kept before the daily auto-purge. */
import { useState } from "react";

import { useAppSettings, useUpdateAppSettings } from "@/api/appSettings";
import { ApiError } from "@/api/client";
import type { AppSettings } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

export function TrashRetentionCard() {
  const settings = useAppSettings();
  if (settings.isLoading) return <Skeleton className="h-40 w-full rounded-xl" />;
  if (!settings.data) return null;
  return <Form initial={settings.data} />;
}

function Form({ initial }: { initial: AppSettings }) {
  const update = useUpdateAppSettings();
  const [days, setDays] = useState(String(initial.trash_retention_days));
  const n = Number(days);
  const invalid = days.trim() === "" || !Number.isInteger(n) || n < 0 || n > 3650;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Trash retention</CardTitle>
        <CardDescription>
          Deleted models stay in the trash for this many days, then are permanently removed along
          with their files. 0 keeps them until you empty the trash yourself.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="trash-retention">Retention (days)</Label>
          <Input
            id="trash-retention"
            type="number"
            min={0}
            max={3650}
            value={days}
            onChange={(e) => setDays(e.target.value)}
            aria-invalid={invalid}
            className="w-32"
          />
          {invalid ? (
            <p role="alert" className="text-xs text-destructive">
              Enter a whole number from 0 to 3650.
            </p>
          ) : null}
        </div>
        <div className="flex items-center gap-3">
          <Button
            type="button"
            disabled={invalid || update.isPending}
            onClick={() => update.mutate({ ...initial, trash_retention_days: n })}
          >
            {update.isPending ? "Saving…" : "Save"}
          </Button>
          {update.isSuccess && <p className="text-sm text-success">Saved.</p>}
        </div>
        {update.isError && (
          <p role="alert" className="text-sm text-destructive">
            {update.error instanceof ApiError ? update.error.detail : "Could not save this setting."}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
