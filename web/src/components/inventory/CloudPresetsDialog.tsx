/**
 * "Cloud presets" dialog: import and resync the user's own printer / process /
 * filament presets from Orca Cloud (device-code pairing) or Bambu Cloud (the
 * account connected in Settings) as local slicer profiles. Each preset shows
 * its sync status against the local copy and can be diffed before importing;
 * presets edited locally are protected unless "overwrite local edits" is on.
 */
import { useIsMutating } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { CloudDownloadIcon, RefreshCwIcon } from "lucide-react";

import { ApiError } from "@/api/client";
import {
  useCloudDiff,
  CLOUD_MUTATION_KEY,
  useCloudPresets,
  useCloudStatus,
  useCloudSync,
  useOrcaDeviceStart,
  useOrcaDevicePoll,
  useOrcaDisconnect,
  type CloudPreset,
  type CloudPresetStatus,
  type CloudSource,
  type CloudSyncResult,
  type DeviceStart,
} from "@/api/cloudPresets";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DialogClose,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

const STATUS_LABEL: Record<CloudPresetStatus, string> = {
  new: "New",
  up_to_date: "Up to date",
  remote_changed: "Changed in cloud",
  local_modified: "Edited locally",
  conflict: "Changed on both sides",
  removed: "Removed from cloud",
};

function StatusBadge({ status }: { status: CloudPresetStatus }) {
  const variant =
    status === "up_to_date" ? "outline" : status === "conflict" || status === "removed" ? "destructive" : "secondary";
  return <Badge variant={variant}>{STATUS_LABEL[status]}</Badge>;
}

function show(value: unknown): string {
  if (value === undefined || value === null) return "—";
  const s = typeof value === "string" ? value : JSON.stringify(value);
  return s.length > 80 ? `${s.slice(0, 80)}…` : s;
}

function errorText(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.detail : fallback;
}

function DiffRow({ source, presetId }: { source: CloudSource; presetId: string }) {
  const diff = useCloudDiff(source, presetId);
  if (diff.isLoading) return <Skeleton className="h-8 w-full" />;
  if (diff.isError) {
    return (
      <p role="alert" className="text-xs text-destructive">
        {errorText(diff.error, "Couldn't load the diff.")}
      </p>
    );
  }
  const changes = diff.data?.changes ?? [];
  if (changes.length === 0) return <p className="text-xs text-muted-foreground">No differences.</p>;
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-muted-foreground">
          <th className="pr-2 font-medium">Setting</th>
          <th className="pr-2 font-medium">Local</th>
          <th className="font-medium">Cloud</th>
        </tr>
      </thead>
      <tbody>
        {changes.map((c) => (
          <tr key={c.key} className="align-top">
            <td className="pr-2 font-mono">{c.key}</td>
            <td className="pr-2 font-mono text-muted-foreground">{show(c.local)}</td>
            <td className="font-mono">{show(c.remote)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function OrcaConnect() {
  const start = useOrcaDeviceStart();
  const poll = useOrcaDevicePoll();
  const [device, setDevice] = useState<DeviceStart | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const intervalMs = useRef(5000);

  // Poll the token endpoint while a pairing is awaiting approval.
  useEffect(() => {
    if (!device) return;
    intervalMs.current = Math.max(device.interval, 1) * 1000;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      poll.mutate(undefined, {
        onSuccess: (r) => {
          if (stopped) return;
          if (r.status === "pending") {
            intervalMs.current = Math.max(r.interval, 1) * 1000;
            timer = setTimeout(tick, intervalMs.current);
            return;
          }
          setDevice(null);
          if (r.status === "denied") setMessage("Pairing was denied in Orca Cloud.");
          if (r.status === "expired") setMessage("The pairing code expired. Start again.");
        },
        onError: (e) => {
          if (stopped) return;
          setDevice(null);
          setMessage(errorText(e, "Pairing failed."));
        },
      });
    };
    timer = setTimeout(tick, intervalMs.current);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `poll` is stable enough; re-run per pairing only
  }, [device]);

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted-foreground">
        Pair this server with your Orca Cloud account (read-only). You approve a short code in your Orca Cloud
        settings; no password is shared.
      </p>
      {device ? (
        <div className="rounded-lg border p-3 text-sm">
          <p>
            Enter this code in Orca Cloud:{" "}
            <span className="font-mono text-base font-semibold">{device.user_code}</span>
          </p>
          <p className="mt-1">
            <a
              className="underline"
              href={device.verification_uri_complete ?? device.verification_uri}
              target="_blank"
              rel="noreferrer"
            >
              Open the approval page
            </a>
          </p>
          <p className="mt-1 text-xs text-muted-foreground">Waiting for approval…</p>
        </div>
      ) : (
        <div>
          <Button
            type="button"
            size="sm"
            disabled={start.isPending}
            onClick={() => {
              setMessage(null);
              start.mutate(undefined, {
                onSuccess: setDevice,
                onError: (e) => setMessage(errorText(e, "Couldn't start pairing.")),
              });
            }}
          >
            {start.isPending ? "Starting…" : "Connect Orca Cloud"}
          </Button>
        </div>
      )}
      {message ? (
        <p role="alert" className="text-sm text-destructive">
          {message}
        </p>
      ) : null}
    </div>
  );
}

function SourcePanel({ source, canWrite, canAdmin }: { source: CloudSource; canWrite: boolean; canAdmin: boolean }) {
  const status = useCloudStatus();
  const connected = status.data?.[source].connected ?? false;
  const needsReconnect = status.data?.[source].needs_reconnect ?? false;
  const list = useCloudPresets(source, connected && !needsReconnect);
  const sync = useCloudSync(source);
  const disconnect = useOrcaDisconnect();
  const [overwrite, setOverwrite] = useState(false);
  const [diffId, setDiffId] = useState<string | null>(null);
  const [result, setResult] = useState<CloudSyncResult | null>(null);
  const [syncError, setSyncError] = useState<string | null>(null);

  function run(presetIds?: string[]) {
    setResult(null);
    setSyncError(null);
    sync.mutate(
      { preset_ids: presetIds, overwrite_local: overwrite },
      { onSuccess: setResult, onError: (e) => setSyncError(errorText(e, "Sync failed.")) },
    );
  }

  if (status.isLoading) return <Skeleton className="h-24 w-full" />;
  if (!connected || needsReconnect) {
    if (source === "orca") return canAdmin ? <OrcaConnect /> : <p className="text-sm text-muted-foreground">Orca Cloud isn&apos;t connected. Ask an admin to connect it.</p>;
    return (
      <p className="text-sm text-muted-foreground">
        {needsReconnect
          ? "Your Bambu sign-in expired. Sign in again under Settings → Accounts."
          : "Connect your Bambu Lab account under Settings → Accounts to import your cloud presets."}
      </p>
    );
  }

  const presets = list.data?.presets ?? [];
  const pending = presets.filter((p) => p.status === "new" || p.status === "remote_changed").length;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          {source === "bambu" ? `Bambu account ${status.data?.bambu.account ?? ""}. ` : "Orca Cloud connected. "}
          {list.isSuccess ? `${presets.length} preset${presets.length === 1 ? "" : "s"}, ${pending} to import.` : null}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" variant="ghost" onClick={() => void list.refetch()}>
            <RefreshCwIcon className="size-4" />
            Refresh
          </Button>
          {canWrite ? (
            <Button type="button" size="sm" disabled={sync.isPending || pending === 0} onClick={() => run()}>
              <CloudDownloadIcon className="size-4" />
              {sync.isPending ? "Importing…" : `Import new & changed (${pending})`}
            </Button>
          ) : null}
          {source === "orca" && canAdmin ? (
            <Button type="button" size="sm" variant="outline" onClick={() => disconnect.mutate()}>
              Disconnect
            </Button>
          ) : null}
        </div>
      </div>

      {canWrite ? (
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />
          Overwrite local edits
        </label>
      ) : null}

      {result ? (
        <div role="status" className="text-sm">
          Imported: {result.created} new, {result.updated} updated
          {result.skipped.length > 0
            ? `, ${result.skipped.length} kept (${result.skipped.map((s) => s.name).join(", ")} — edited locally)`
            : ""}
          {result.failed.length > 0 ? `, ${result.failed.length} failed` : ""}.
        </div>
      ) : null}
      {syncError ? (
        <p role="alert" className="text-sm text-destructive">
          {syncError}
        </p>
      ) : null}

      {list.isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : list.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {errorText(list.error, "Couldn't load cloud presets.")}
        </p>
      ) : presets.length === 0 ? (
        <p className="py-4 text-center text-sm text-muted-foreground">No cloud presets found.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Kind</TableHead>
              <TableHead>Status</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {presets.map((p: CloudPreset) => (
              <PresetRow
                key={p.preset_id}
                preset={p}
                source={source}
                canWrite={canWrite}
                diffOpen={diffId === p.preset_id}
                busy={sync.isPending}
                onDiff={() => setDiffId(diffId === p.preset_id ? null : p.preset_id)}
                onImport={() => run([p.preset_id])}
              />
            ))}
          </TableBody>
        </Table>
      )}
      {(list.data?.removed ?? []).length > 0 ? (
        <p className="text-xs text-muted-foreground">
          No longer in the cloud (local copies kept): {(list.data?.removed ?? []).map((r) => r.name).join(", ")}
        </p>
      ) : null}
    </div>
  );
}

function PresetRow({
  preset,
  source,
  canWrite,
  diffOpen,
  busy,
  onDiff,
  onImport,
}: {
  preset: CloudPreset;
  source: CloudSource;
  canWrite: boolean;
  diffOpen: boolean;
  busy: boolean;
  onDiff: () => void;
  onImport: () => void;
}) {
  const imported = preset.profile_id != null;
  return (
    <>
      <TableRow>
        <TableCell className="font-medium">{preset.name}</TableCell>
        <TableCell className="capitalize text-muted-foreground">{preset.kind}</TableCell>
        <TableCell>
          <StatusBadge status={preset.status} />
        </TableCell>
        <TableCell className="text-right">
          <div className="flex justify-end gap-1">
            <Button type="button" size="xs" variant="ghost" aria-label={`Diff ${preset.name}`} onClick={onDiff}>
              {diffOpen ? "Hide diff" : "Diff"}
            </Button>
            {canWrite && preset.status !== "up_to_date" ? (
              <Button
                type="button"
                size="xs"
                variant="outline"
                aria-label={`${imported ? "Resync" : "Import"} ${preset.name}`}
                disabled={busy}
                onClick={onImport}
              >
                {imported ? "Resync" : "Import"}
              </Button>
            ) : null}
          </div>
        </TableCell>
      </TableRow>
      {diffOpen ? (
        <TableRow>
          <TableCell colSpan={4}>
            <DiffRow source={source} presetId={preset.preset_id} />
          </TableCell>
        </TableRow>
      ) : null}
    </>
  );
}

export function CloudPresetsDialog({
  open,
  onOpenChange,
  canWrite,
  canAdmin,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  canWrite: boolean;
  canAdmin: boolean;
}) {
  const [tab, setTab] = useState<CloudSource>("orca");
  // Any in-flight sync / pairing mutation blocks closing; nothing is "dirty" (each preset imports immediately).
  const busy = useIsMutating({ mutationKey: CLOUD_MUTATION_KEY }) > 0;
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} busy={busy} className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Cloud presets</DialogTitle>
          <DialogDescription>
            Import your own printer, process and filament presets from Orca Cloud or Bambu Cloud. Resync later to
            pick up changes; presets you edited here are kept unless you overwrite them.
          </DialogDescription>
        </DialogHeader>
        <Tabs value={tab} onValueChange={(v) => setTab(v as CloudSource)}>
          <TabsList>
            <TabsTrigger value="orca">Orca Cloud</TabsTrigger>
            <TabsTrigger value="bambu">Bambu Cloud</TabsTrigger>
          </TabsList>
          <TabsContent value="orca">
            <SourcePanel source="orca" canWrite={canWrite} canAdmin={canAdmin} />
          </TabsContent>
          <TabsContent value="bambu">
            <SourcePanel source="bambu" canWrite={canWrite} canAdmin={canAdmin} />
          </TabsContent>
        </Tabs>
        <DialogFooter>
          <DialogClose asChild>
            <Button type="button" variant="outline">Close</Button>
          </DialogClose>
        </DialogFooter>
    </FormDialog>
  );
}
