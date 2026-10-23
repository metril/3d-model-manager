/**
 * Spoolman connector card (Q9): server URL, sync mode (off / mirror /
 * authoritative), Test connection and Sync now.
 */
import { useEffect, useState } from "react";

import { ApiError } from "@/api/client";
import {
  type SpoolmanMode,
  useSpoolmanSettings,
  useSpoolmanSync,
  useSpoolmanTest,
  useUpdateSpoolmanSettings,
} from "@/api/spoolman";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

const MODE_HELP: Record<SpoolmanMode, string> = {
  off: "Disabled -- nothing is sent to or read from Spoolman.",
  mirror: "Usage and AMS locations are pushed to Spoolman; local weights stay authoritative.",
  authoritative: "As mirror, and Spoolman's remaining weight overwrites the local one.",
};

export function SpoolmanCard() {
  const settings = useSpoolmanSettings();
  const update = useUpdateSpoolmanSettings();
  const test = useSpoolmanTest();
  const sync = useSpoolmanSync();
  const [url, setUrl] = useState("");
  const [mode, setMode] = useState<SpoolmanMode>("off");
  const [seeded, setSeeded] = useState(false);

  useEffect(() => {
    if (settings.data && !seeded) {
      setUrl(settings.data.url ?? "");
      setMode(settings.data.mode);
      setSeeded(true);
    }
  }, [settings.data, seeded]);

  if (settings.isLoading) return <Skeleton className="h-48 w-full rounded-xl" />;
  if (settings.isError || !settings.data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Spoolman</CardTitle>
          <CardDescription>
            {settings.error instanceof ApiError ? settings.error.detail : "Couldn't load these settings."}
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const trimmed = url.trim();
  const saved = settings.data;
  const dirty = trimmed !== (saved.url ?? "") || mode !== saved.mode;
  const enabled = saved.mode !== "off" && !!saved.url;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Spoolman</CardTitle>
        <CardDescription>
          Sync spool usage and AMS locations with a Spoolman server. Spools are linked individually on the
          Spools page.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="spoolman-url">Server URL</Label>
            <Input
              id="spoolman-url"
              placeholder="http://spoolman.local:7912"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="spoolman-mode">Mode</Label>
            <select
              id="spoolman-mode"
              className="h-9 rounded-md border border-input bg-background px-3 text-sm"
              value={mode}
              onChange={(e) => setMode(e.target.value as SpoolmanMode)}
            >
              <option value="off">Off</option>
              <option value="mirror">Mirror</option>
              <option value="authoritative">Spoolman authoritative</option>
            </select>
            <p className="text-xs text-muted-foreground">{MODE_HELP[mode]}</p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            disabled={update.isPending || !dirty}
            onClick={() => update.mutate({ url: trimmed || null, mode })}
          >
            {update.isPending ? "Saving…" : "Save"}
          </Button>
          <Button type="button" variant="outline" disabled={!trimmed || test.isPending} onClick={() => test.mutate(trimmed)}>
            Test connection
          </Button>
          <Button type="button" variant="outline" disabled={!enabled || sync.isPending} onClick={() => sync.mutate()}>
            {sync.isPending ? "Syncing…" : "Sync now"}
          </Button>
        </div>

        {update.isSuccess && !dirty && <p className="text-sm text-emerald-600 dark:text-emerald-400">Saved.</p>}
        {update.isError && (
          <p role="alert" className="text-sm text-destructive">
            {update.error instanceof ApiError ? update.error.detail : "Could not save these settings."}
          </p>
        )}
        {test.data &&
          (test.data.ok ? (
            <p className="text-sm text-emerald-600 dark:text-emerald-400">
              Connected{test.data.version ? ` (Spoolman ${test.data.version})` : ""}.
            </p>
          ) : (
            <p role="alert" className="text-sm text-destructive">
              Connection failed: {test.data.error}
            </p>
          ))}
        {sync.data && (
          <p className="text-sm text-muted-foreground">
            Synced {sync.data.pulled} spool(s){sync.data.errors ? `, ${sync.data.errors} error(s)` : ""}.
          </p>
        )}
        {sync.isError && (
          <p role="alert" className="text-sm text-destructive">
            {sync.error instanceof ApiError ? sync.error.detail : "Sync failed."}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
