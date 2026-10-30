/** Locations tab (T5): CRUD table; per-location HA sensor sub-section with readings + alert badge. */
import { Fragment, useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import {
  useCreateLocation,
  useCreateSensor,
  useDeleteLocation,
  useDeleteSensor,
  useHaEntities,
  useLocationReadings,
  useLocations,
  useLocationSensors,
  usePatchLocation,
  usePatchSensor,
} from "@/api/spoolAdmin";
import type { LocationSensor, SensorKind, StorageLocation } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime } from "@/lib/format";

function sensorKind(deviceClass: string | null | undefined): SensorKind {
  return deviceClass === "temperature" || deviceClass === "humidity" ? deviceClass : "other";
}

function LocationDialog({ open, onOpenChange, location }: { open: boolean; onOpenChange: (o: boolean) => void; location?: StorageLocation }) {
  const { t } = useTranslation();
  const create = useCreateLocation();
  const patch = usePatchLocation();
  const mutation = location ? patch : create;
  const [name, setName] = useState("");
  const [identifier, setIdentifier] = useState("");

  const snapL = (n: string, i: string) => JSON.stringify([n, i]);
  const [baseline, setBaseline] = useState(() => snapL(location?.name ?? "", location?.identifier ?? ""));
  const dirty = snapL(name, identifier) !== baseline;

  useEffect(() => {
    if (open) {
      setBaseline(snapL(location?.name ?? "", location?.identifier ?? ""));
      setName(location?.name ?? "");
      setIdentifier(location?.identifier ?? "");
      create.reset();
      patch.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function submit(e: FormEvent) {
    e.preventDefault();
    const payload = { name: name.trim(), identifier: identifier.trim() || null };
    const done = { onSuccess: () => onOpenChange(false) };
    if (location) patch.mutate({ id: location.id, payload }, done);
    else create.mutate(payload, done);
  }

  return (
    <FormDialog open={open} onOpenChange={onOpenChange} dirty={dirty} busy={mutation.isPending} className="sm:max-w-md">
        <form onSubmit={submit} className="flex flex-col gap-3">
          <DialogHeader>
            <DialogTitle>{location ? t("spoolAdmin.loc.editTitle") : t("spoolAdmin.loc.newTitle")}</DialogTitle>
            <DialogDescription>{t("spoolAdmin.loc.dialogDesc")}</DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="loc-name">{t("spoolAdmin.common.name")}</Label>
            <Input id="loc-name" required value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="loc-ident">{t("spoolAdmin.loc.identifier")}</Label>
            <Input id="loc-ident" value={identifier} onChange={(e) => setIdentifier(e.target.value)} />
          </div>
          {mutation.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {mutation.error instanceof ApiError ? mutation.error.detail : t("spoolAdmin.common.saveFailed")}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="submit" disabled={mutation.isPending || !name.trim()}>
              {mutation.isPending ? t("spoolAdmin.common.saving") : t("spoolAdmin.common.save")}
            </Button>
          </DialogFooter>
        </form>
    </FormDialog>
  );
}

function SensorDialog({
  open,
  onOpenChange,
  locationId,
  sensor,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  locationId: number;
  sensor?: LocationSensor;
}) {
  const { t } = useTranslation();
  const entities = useHaEntities(open && !sensor);
  const create = useCreateSensor(locationId);
  const patch = usePatchSensor();
  const mutation = sensor ? patch : create;
  const [entityId, setEntityId] = useState("");
  const [name, setName] = useState("");
  const [above, setAbove] = useState("");
  const [below, setBelow] = useState("");
  const [notify, setNotify] = useState(false);
  const [showOnCard, setShowOnCard] = useState(true);

  const sensorInitial = () => [
    sensor?.entity_id ?? "",
    sensor?.name ?? "",
    sensor?.alert_above != null ? String(sensor.alert_above) : "",
    sensor?.alert_below != null ? String(sensor.alert_below) : "",
    sensor?.notify_on_alert ?? false,
    sensor?.show_on_card ?? true,
  ];
  const [baseline, setBaseline] = useState(() => JSON.stringify(sensorInitial()));
  const dirty = JSON.stringify([entityId, name, above, below, notify, showOnCard]) !== baseline;

  useEffect(() => {
    if (open) {
      setBaseline(JSON.stringify(sensorInitial()));
      setEntityId(sensor?.entity_id ?? "");
      setName(sensor?.name ?? "");
      setAbove(sensor?.alert_above != null ? String(sensor.alert_above) : "");
      setBelow(sensor?.alert_below != null ? String(sensor.alert_below) : "");
      setNotify(sensor?.notify_on_alert ?? false);
      setShowOnCard(sensor?.show_on_card ?? true);
      create.reset();
      patch.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function pickEntity(id: string) {
    setEntityId(id);
    const e = (entities.data ?? []).find((x) => x.entity_id === id);
    if (e && !name.trim()) setName(e.friendly_name ?? e.entity_id);
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    const num = (v: string) => (v.trim() === "" ? null : Number(v));
    const ent = (entities.data ?? []).find((x) => x.entity_id === entityId);
    const payload = {
      name: name.trim(),
      entity_id: entityId,
      kind: sensorKind(ent?.device_class ?? sensor?.device_class ?? sensor?.kind),
      alert_above: num(above),
      alert_below: num(below),
      notify_on_alert: notify,
      show_on_card: showOnCard,
    };
    const done = { onSuccess: () => onOpenChange(false) };
    if (sensor) patch.mutate({ id: sensor.id, payload }, done);
    else create.mutate(payload, done);
  }

  return (
    <FormDialog open={open} onOpenChange={onOpenChange} dirty={dirty} busy={mutation.isPending} className="sm:max-w-lg">
        <form onSubmit={submit} className="flex flex-col gap-3">
          <DialogHeader>
            <DialogTitle>{sensor ? t("spoolAdmin.sensor.editTitle") : t("spoolAdmin.sensor.newTitle")}</DialogTitle>
            <DialogDescription>{t("spoolAdmin.sensor.dialogDesc")}</DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="sensor-entity">{t("spoolAdmin.sensor.entity")}</Label>
            {sensor ? (
              <Input id="sensor-entity" value={entityId} readOnly />
            ) : entities.isError ? (
              <>
                <Input
                  id="sensor-entity"
                  value={entityId}
                  onChange={(e) => setEntityId(e.target.value.trim())}
                  placeholder="sensor.example_temperature"
                  autoComplete="off"
                />
                <p className="text-xs text-muted-foreground">
                  {entities.error instanceof ApiError ? entities.error.detail : t("spoolAdmin.sensor.entitiesFailed")}
                  {" — "}
                  {t("spoolAdmin.sensor.entityManualHint")}
                </p>
              </>
            ) : (
              <Select aria-label={t("spoolAdmin.sensor.entity")} value={entityId} onValueChange={pickEntity}>
                <SelectTrigger id="sensor-entity" aria-label={t("spoolAdmin.sensor.entity")} className="w-full">
                  <SelectValue placeholder={t("spoolAdmin.sensor.pickEntity")} />
                </SelectTrigger>
                <SelectContent>
                  {(entities.data ?? []).map((e) => (
                    <SelectItem key={e.entity_id} value={e.entity_id}>
                      {e.friendly_name ?? e.entity_id} ({e.entity_id}){e.state != null ? ` · ${e.state}${e.unit ?? ""}` : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="sensor-name">{t("spoolAdmin.common.name")}</Label>
            <Input id="sensor-name" required value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="sensor-above">{t("spoolAdmin.sensor.alertAbove")}</Label>
              <Input id="sensor-above" type="number" step="any" value={above} onChange={(e) => setAbove(e.target.value)} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="sensor-below">{t("spoolAdmin.sensor.alertBelow")}</Label>
              <Input id="sensor-below" type="number" step="any" value={below} onChange={(e) => setBelow(e.target.value)} />
            </div>
          </div>
          <label className="flex items-center justify-between text-sm">
            {t("spoolAdmin.sensor.showOnCard")}
            <Switch aria-label={t("spoolAdmin.sensor.showOnCard")} checked={showOnCard} onCheckedChange={setShowOnCard} />
          </label>
          <label className="flex items-center justify-between text-sm">
            {t("spoolAdmin.sensor.notify")}
            <Switch aria-label={t("spoolAdmin.sensor.notify")} checked={notify} onCheckedChange={setNotify} />
          </label>
          {mutation.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {mutation.error instanceof ApiError ? mutation.error.detail : t("spoolAdmin.common.saveFailed")}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="submit" disabled={mutation.isPending || !entityId || !name.trim()}>
              {mutation.isPending ? t("spoolAdmin.common.saving") : t("spoolAdmin.common.save")}
            </Button>
          </DialogFooter>
        </form>
    </FormDialog>
  );
}

function SensorSection({ location, canWrite }: { location: StorageLocation; canWrite: boolean }) {
  const { t } = useTranslation();
  const sensors = useLocationSensors(location.id);
  const readings = useLocationReadings(location.id);
  const del = useDeleteSensor();
  const [editing, setEditing] = useState<LocationSensor | undefined>();
  const [open, setOpen] = useState(false);
  const reading = (id: number) => (readings.data ?? []).find((r) => r.sensor_id === id);

  return (
    <div className="flex flex-col gap-2 py-2">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">{t("spoolAdmin.sensor.title", { name: location.name })}</h3>
        {canWrite ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => {
              setEditing(undefined);
              setOpen(true);
            }}
          >
            {t("spoolAdmin.sensor.add")}
          </Button>
        ) : null}
      </div>
      {sensors.isLoading ? (
        <Skeleton className="h-10 w-full" />
      ) : (sensors.data ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("spoolAdmin.sensor.empty")}</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {(sensors.data ?? []).map((s) => {
            const r = reading(s.id);
            const alerting = r?.alerting ?? s.alerting;
            const state = r?.state ?? s.last_state;
            return (
              <li key={s.id} className="flex items-center gap-3 rounded-md border px-2 py-1.5 text-sm">
                <span className="font-medium">{s.name}</span>
                <span className="font-mono text-xs text-muted-foreground">{s.entity_id}</span>
                <span className="ml-auto font-mono tabular-nums">
                  {state != null ? `${state}${r?.unit ?? s.unit ?? ""}` : "—"}
                </span>
                {alerting ? <Badge variant="destructive">{t("spoolAdmin.sensor.alert")}</Badge> : null}
                <span className="text-xs text-muted-foreground">
                  {s.alert_below != null ? `< ${s.alert_below} ` : ""}
                  {s.alert_above != null ? `> ${s.alert_above}` : ""}
                </span>
                <span className="text-xs text-muted-foreground">{formatDateTime(r?.last_changed_at ?? s.last_changed_at)}</span>
                {canWrite ? (
                  <>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-label={t("spoolAdmin.common.editNamed", { name: s.name })}
                      onClick={() => {
                        setEditing(s);
                        setOpen(true);
                      }}
                    >
                      {t("spoolAdmin.common.edit")}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-label={t("spoolAdmin.common.deleteNamed", { name: s.name })}
                      onClick={() =>
                        del.mutate(s.id, {
                          onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("spoolAdmin.common.deleteFailed")),
                        })
                      }
                    >
                      {t("spoolAdmin.common.delete")}
                    </Button>
                  </>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      <SensorDialog open={open} onOpenChange={setOpen} locationId={location.id} sensor={editing} />
    </div>
  );
}

export function LocationsPanel() {
  const { t } = useTranslation();
  const { can } = useAuth();
  const canWrite = can("inventory:write");
  const locations = useLocations();
  const del = useDeleteLocation();
  const [editing, setEditing] = useState<StorageLocation | undefined>();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleting, setDeleting] = useState<StorageLocation | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">{t("spoolAdmin.loc.desc")}</p>
        {canWrite ? (
          <Button
            type="button"
            size="sm"
            onClick={() => {
              setEditing(undefined);
              setDialogOpen(true);
            }}
          >
            {t("spoolAdmin.loc.add")}
          </Button>
        ) : null}
      </div>
      {locations.isLoading ? (
        <Skeleton className="h-24 w-full rounded-lg" />
      ) : (locations.data ?? []).length === 0 ? (
        <p className="py-4 text-center text-sm text-muted-foreground">{t("spoolAdmin.loc.emptyTable")}</p>
      ) : (
        <Table className="data-table">
          <TableHeader>
            <TableRow>
              <TableHead>{t("spoolAdmin.common.name")}</TableHead>
              <TableHead>{t("spoolAdmin.loc.identifier")}</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {(locations.data ?? []).map((l) => (
              <Fragment key={l.id}>
                <TableRow>
                  <TableCell className="font-medium">{l.name}</TableCell>
                  <TableCell className="font-mono text-xs">{l.identifier ?? "—"}</TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-1">
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        aria-expanded={expanded === l.id}
                        aria-label={t("spoolAdmin.loc.sensorsFor", { name: l.name })}
                        onClick={() => setExpanded(expanded === l.id ? null : l.id)}
                      >
                        {t("spoolAdmin.loc.sensors")}
                      </Button>
                      {canWrite ? (
                        <>
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            aria-label={t("spoolAdmin.common.editNamed", { name: l.name })}
                            onClick={() => {
                              setEditing(l);
                              setDialogOpen(true);
                            }}
                          >
                            {t("spoolAdmin.common.edit")}
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            aria-label={t("spoolAdmin.common.deleteNamed", { name: l.name })}
                            onClick={() => setDeleting(l)}
                          >
                            {t("spoolAdmin.common.delete")}
                          </Button>
                        </>
                      ) : null}
                    </div>
                  </TableCell>
                </TableRow>
                {expanded === l.id ? (
                  <TableRow>
                    <TableCell colSpan={3} className="bg-muted/20">
                      <SensorSection location={l} canWrite={canWrite} />
                    </TableCell>
                  </TableRow>
                ) : null}
              </Fragment>
            ))}
          </TableBody>
        </Table>
      )}
      <LocationDialog open={dialogOpen} onOpenChange={setDialogOpen} location={editing} />
      <ConfirmDialog
        open={deleting != null}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={t("spoolAdmin.loc.deleteTitle", { name: deleting?.name ?? "" })}
        description={t("spoolAdmin.loc.deleteDesc")}
        confirmLabel={t("spoolAdmin.common.delete")}
        destructive
        onConfirm={() => {
          if (deleting)
            del.mutate(deleting.id, {
              onSuccess: () => toast.success(t("spoolAdmin.common.deleted")),
              onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("spoolAdmin.common.deleteFailed")),
            });
        }}
      />
    </div>
  );
}
