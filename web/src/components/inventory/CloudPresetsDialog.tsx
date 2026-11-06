/**
 * "Cloud presets" dialog: import and resync the user's own printer / process /
 * filament presets from Orca Cloud (device-code pairing) or Bambu Cloud (the
 * account connected in Settings) as local slicer profiles. Each preset shows
 * its sync status against the local copy and can be diffed before importing;
 * presets edited locally are protected unless "overwrite local edits" is on.
 */
import { useIsMutating } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
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

const STATUS_KEY: Record<CloudPresetStatus, string> = {
  new: "cloudPresets.status.new",
  up_to_date: "cloudPresets.status.up_to_date",
  remote_changed: "cloudPresets.status.remote_changed",
  local_modified: "cloudPresets.status.local_modified",
  conflict: "cloudPresets.status.conflict",
  removed: "cloudPresets.status.removed",
};

function StatusBadge({ status }: { status: CloudPresetStatus }) {
  const { t } = useTranslation();
  const variant =
    status === "up_to_date" ? "outline" : status === "conflict" || status === "removed" ? "destructive" : "secondary";
  return <Badge variant={variant}>{t(STATUS_KEY[status])}</Badge>;
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
  const { t } = useTranslation();
  const diff = useCloudDiff(source, presetId);
  if (diff.isLoading) return <Skeleton className="h-8 w-full" />;
  if (diff.isError) {
    return (
      <p role="alert" className="text-xs text-destructive">
        {errorText(diff.error, t("cloudPresets.diffFailed"))}
      </p>
    );
  }
  const changes = diff.data?.changes ?? [];
  if (changes.length === 0) return <p className="text-xs text-muted-foreground">{t("cloudPresets.noDiff")}</p>;
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-muted-foreground">
          <th className="pr-2 font-medium">{t("cloudPresets.setting")}</th>
          <th className="pr-2 font-medium">{t("cloudPresets.local")}</th>
          <th className="font-medium">{t("cloudPresets.cloud")}</th>
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

function OrcaConnect({ onActive }: { onActive?: (active: boolean) => void }) {
  const { t } = useTranslation();
  const start = useOrcaDeviceStart();
  const poll = useOrcaDevicePoll();
  const [device, setDevice] = useState<DeviceStart | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  // A pending device-code login is progress Escape must not silently drop.
  useEffect(() => onActive?.(device !== null), [device, onActive]);
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
          if (r.status === "denied") setMessage(t("cloudPresets.denied"));
          if (r.status === "expired") setMessage(t("cloudPresets.expired"));
        },
        onError: (e) => {
          if (stopped) return;
          setDevice(null);
          setMessage(errorText(e, t("cloudPresets.pairFailed")));
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
        {t("cloudPresets.pairIntro")}
      </p>
      {device ? (
        <div className="rounded-lg border p-3 text-sm">
          <p>
            {t("cloudPresets.enterCode")}{" "}
            <span className="font-mono text-base font-semibold">{device.user_code}</span>
          </p>
          <p className="mt-1">
            <a
              className="underline"
              href={device.verification_uri_complete ?? device.verification_uri}
              target="_blank"
              rel="noreferrer"
            >
              {t("cloudPresets.openApproval")}
            </a>
          </p>
          <p className="mt-1 text-xs text-muted-foreground">{t("cloudPresets.waiting")}</p>
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
                onError: (e) => setMessage(errorText(e, t("cloudPresets.startFailed"))),
              });
            }}
          >
            {start.isPending ? t("cloudPresets.starting") : t("cloudPresets.connect")}
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

function SourcePanel({
  source,
  canWrite,
  canAdmin,
  onActive,
}: {
  source: CloudSource;
  canWrite: boolean;
  canAdmin: boolean;
  onActive: (key: string, active: boolean) => void;
}) {
  const { t } = useTranslation();
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
  useEffect(() => onActive(`${source}-sync`, result !== null), [result, source, onActive]);

  function run(presetIds?: string[]) {
    setResult(null);
    setSyncError(null);
    sync.mutate(
      { preset_ids: presetIds, overwrite_local: overwrite },
      { onSuccess: setResult, onError: (e) => setSyncError(errorText(e, t("cloudPresets.syncFailed"))) },
    );
  }

  if (status.isLoading) return <Skeleton className="h-24 w-full" />;
  if (!connected || needsReconnect) {
    if (source === "orca") return canAdmin ? <OrcaConnect onActive={(a) => onActive(`${source}-login`, a)} /> : <p className="text-sm text-muted-foreground">{t("cloudPresets.orcaDisconnected")}</p>;
    return (
      <p className="text-sm text-muted-foreground">
        {needsReconnect
          ? t("cloudPresets.bambuExpired")
          : t("cloudPresets.bambuConnect")}
      </p>
    );
  }

  const presets = list.data?.presets ?? [];
  const pending = presets.filter((p) => p.status === "new" || p.status === "remote_changed").length;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          {source === "bambu" ? t("cloudPresets.bambuAccount", { account: status.data?.bambu.account ?? "" }) : t("cloudPresets.orcaConnected")}
          {list.isSuccess ? t("cloudPresets.summary", { count: presets.length, pending }) : null}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" variant="ghost" onClick={() => void list.refetch()}>
            <RefreshCwIcon className="size-4" />
            {t("cloudPresets.refresh")}
          </Button>
          {canWrite ? (
            <Button type="button" size="sm" disabled={sync.isPending || pending === 0} onClick={() => run()}>
              <CloudDownloadIcon className="size-4" />
              {sync.isPending ? t("cloudPresets.importing") : t("cloudPresets.importAll", { pending })}
            </Button>
          ) : null}
          {source === "orca" && canAdmin ? (
            <Button type="button" size="sm" variant="outline" onClick={() => disconnect.mutate()}>
              {t("cloudPresets.disconnect")}
            </Button>
          ) : null}
        </div>
      </div>

      {canWrite ? (
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />
          {t("cloudPresets.overwrite")}
        </label>
      ) : null}

      {result ? (
        <div role="status" className="text-sm">
          {t("cloudPresets.imported", { created: result.created, updated: result.updated })}
          {result.skipped.length > 0
            ? t("cloudPresets.keptPart", { n: result.skipped.length, names: result.skipped.map((s) => s.name).join(", ") })
            : ""}
          {result.failed.length > 0 ? t("cloudPresets.failedPart", { n: result.failed.length }) : ""}.
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
          {errorText(list.error, t("cloudPresets.loadFailed"))}
        </p>
      ) : presets.length === 0 ? (
        <p className="py-4 text-center text-sm text-muted-foreground">{t("cloudPresets.none")}</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("cloudPresets.name")}</TableHead>
              <TableHead>{t("cloudPresets.kind")}</TableHead>
              <TableHead>{t("cloudPresets.statusHead")}</TableHead>
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
          {t("cloudPresets.removed")} {(list.data?.removed ?? []).map((r) => r.name).join(", ")}
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
  const { t } = useTranslation();
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
            <Button type="button" size="xs" variant="ghost" aria-label={t("cloudPresets.diffName", { name: preset.name })} onClick={onDiff}>
              {diffOpen ? t("cloudPresets.hideDiff") : t("cloudPresets.diff")}
            </Button>
            {canWrite && preset.status !== "up_to_date" ? (
              <Button
                type="button"
                size="xs"
                variant="outline"
                aria-label={t(imported ? "cloudPresets.resyncName" : "cloudPresets.importName", { name: preset.name })}
                disabled={busy}
                onClick={onImport}
              >
                {imported ? t("cloudPresets.resync") : t("cloudPresets.import")}
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
  const { t } = useTranslation();
  const [tab, setTab] = useState<CloudSource>("orca");
  // Any in-flight sync / pairing mutation blocks closing; nothing is "dirty" (each preset imports immediately).
  const busy = useIsMutating({ mutationKey: CLOUD_MUTATION_KEY }) > 0;
  // A device-code login in progress or an unread sync result is dirty: Escape asks first.
  const [flags, setFlags] = useState<Record<string, boolean>>({});
  const onActive = useCallback(
    (key: string, active: boolean) => setFlags((f) => (!!f[key] === active ? f : { ...f, [key]: active })),
    [],
  );
  const dirty = Object.values(flags).some(Boolean);
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} dirty={dirty} busy={busy} className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("cloudPresets.title")}</DialogTitle>
          <DialogDescription>
            {t("cloudPresets.desc")}
          </DialogDescription>
        </DialogHeader>
        <Tabs value={tab} onValueChange={(v) => setTab(v as CloudSource)}>
          <TabsList>
            <TabsTrigger value="orca">{t("cloudPresets.orcaTab")}</TabsTrigger>
            <TabsTrigger value="bambu">{t("cloudPresets.bambuTab")}</TabsTrigger>
          </TabsList>
          <TabsContent value="orca">
            <SourcePanel source="orca" canWrite={canWrite} canAdmin={canAdmin} onActive={onActive} />
          </TabsContent>
          <TabsContent value="bambu">
            <SourcePanel source="bambu" canWrite={canWrite} canAdmin={canAdmin} onActive={onActive} />
          </TabsContent>
        </Tabs>
        <DialogFooter>
          <DialogClose asChild>
            <Button type="button" variant="outline">{t("common.close")}</Button>
          </DialogClose>
        </DialogFooter>
    </FormDialog>
  );
}
