import { useState } from "react";
import { PencilIcon, Trash2Icon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import {
  useDeleteMaintenanceTask,
  useMaintenanceLogs,
  useMaintenanceTasks,
  useMarkMaintenanceDone,
  useSaveMaintenanceTask,
  useSetMaintenanceMode,
} from "@/api/ops";
import type { MaintenanceTask } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

function remaining(task: MaintenanceTask, t: (key: string, opts?: Record<string, unknown>) => string): string {
  const parts: string[] = [];
  if (task.hours_remaining != null) {
    const h = Math.abs(task.hours_remaining).toFixed(1);
    parts.push(task.hours_remaining < 0 ? t("maintenance.hOverdue", { n: h }) : t("maintenance.hLeft", { n: h }));
  }
  if (task.days_remaining != null) {
    const d = Math.abs(task.days_remaining);
    parts.push(task.days_remaining < 0 ? t("maintenance.dOverdue", { n: d }) : t("maintenance.dLeft", { n: d }));
  }
  return parts.length ? parts.join(" / ") : t("maintenance.noInterval");
}

const errText = (e: unknown, fb: string) => (e instanceof ApiError ? e.detail : fb);

function TaskForm({
  task,
  printerId,
  onDone,
}: {
  task?: MaintenanceTask;
  printerId: number;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const save = useSaveMaintenanceTask(printerId);
  const [name, setName] = useState(task?.name ?? "");
  const [hours, setHours] = useState(task?.interval_hours != null ? String(task.interval_hours) : "");
  const [days, setDays] = useState(task?.interval_days != null ? String(task.interval_days) : "");
  const [notes, setNotes] = useState(task?.notes ?? "");
  const [err, setErr] = useState<string | null>(null);
  const id = task ? `mt-${task.id}` : "mt-new";

  function submit() {
    if (!name.trim()) return setErr(t("maintenance.nameRequired"));
    if (!hours.trim() && !days.trim()) return setErr(t("maintenance.intervalRequired"));
    setErr(null);
    save.mutate(
      {
        id: task?.id,
        body: {
          name: name.trim(),
          interval_hours: hours.trim() ? Number(hours) : null,
          interval_days: days.trim() ? Number(days) : null,
          notes: notes.trim() || null,
        },
      },
      { onSuccess: onDone, onError: (e) => setErr(errText(e, t("maintenance.saveError"))) },
    );
  }

  return (
    <div className="grid gap-3 rounded-lg border p-3 sm:grid-cols-3">
      <div className="space-y-1.5 sm:col-span-3">
        <Label htmlFor={`${id}-name`}>{t("maintenance.taskName")}</Label>
        <Input id={`${id}-name`} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={`${id}-hours`}>{t("maintenance.everyPrintHours")}</Label>
        <Input id={`${id}-hours`} type="number" min="0" value={hours} onChange={(e) => setHours(e.target.value)} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={`${id}-days`}>{t("maintenance.everyDays")}</Label>
        <Input id={`${id}-days`} type="number" min="0" value={days} onChange={(e) => setDays(e.target.value)} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={`${id}-notes`}>{t("maintenance.notes")}</Label>
        <Input id={`${id}-notes`} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </div>
      {err ? (
        <p role="alert" className="text-sm text-destructive sm:col-span-3">
          {err}
        </p>
      ) : null}
      <div className="flex gap-2 sm:col-span-3">
        <Button type="button" size="sm" onClick={submit} disabled={save.isPending}>
          {task ? t("maintenance.saveTask") : t("maintenance.addTask")}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onDone}>
          {t("common.cancel")}
        </Button>
      </div>
    </div>
  );
}

function LogList({ printerId, taskId }: { printerId: number; taskId: number }) {
  const { t } = useTranslation();
  const logs = useMaintenanceLogs(printerId, taskId);
  if (logs.isLoading) return <p className="text-xs text-muted-foreground">{t("maintenance.loadingHistory")}</p>;
  if (!logs.data?.length) return <p className="text-xs text-muted-foreground">{t("maintenance.noHistory")}</p>;
  return (
    <ul className="space-y-1 text-xs text-muted-foreground">
      {logs.data.map((l) => (
        <li key={l.id}>
          {new Date(l.done_at).toLocaleDateString()}
          {l.hours_at != null ? ` ${t("maintenance.atHours", { n: l.hours_at.toFixed(1) })}` : ""}
          {l.note ? ` — ${l.note}` : ""}
        </li>
      ))}
    </ul>
  );
}

function TaskRow({ task, printerId, canControl }: { task: MaintenanceTask; printerId: number; canControl: boolean }) {
  const { t } = useTranslation();
  const done = useMarkMaintenanceDone(printerId);
  const del = useDeleteMaintenanceTask(printerId);
  const [editing, setEditing] = useState(false);
  const [noting, setNoting] = useState(false);
  const [note, setNote] = useState("");
  const [showLog, setShowLog] = useState(false);

  if (editing) return <TaskForm task={task} printerId={printerId} onDone={() => setEditing(false)} />;

  const tone =
    task.status === "overdue"
      ? "border-destructive/50 bg-destructive/5"
      : task.status === "soon"
        ? "border-warning/50 bg-warning/5"
        : "";
  return (
    <li className={`space-y-2 rounded-lg border p-3 ${tone}`} data-status={task.status}>
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground">{task.name}</p>
          <p className={`text-xs ${task.status === "overdue" ? "font-medium text-destructive" : "text-muted-foreground"}`}>
            {remaining(task, t)}
          </p>
        </div>
        {canControl ? (
          <>
            <Button type="button" size="sm" onClick={() => setNoting(true)}>
              {t("maintenance.markDone")}
            </Button>
            <Button type="button" size="icon-sm" variant="ghost" aria-label={t("maintenance.editTask", { name: task.name })} onClick={() => setEditing(true)}>
              <PencilIcon className="size-3.5" />
            </Button>
            <ConfirmDialog
              trigger={
                <Button type="button" size="icon-sm" variant="ghost" aria-label={t("maintenance.deleteTask", { name: task.name })}>
                  <Trash2Icon className="size-3.5" />
                </Button>
              }
              title={t("maintenance.deleteTitle", { name: task.name })}
              description={t("maintenance.deleteDesc")}
              confirmLabel={t("common.delete")}
              cancelLabel={t("common.cancel")}
              destructive
              onConfirm={() => del.mutate(task.id)}
            />
          </>
        ) : null}
        <Button type="button" size="sm" variant="ghost" onClick={() => setShowLog((v) => !v)}>
          {showLog ? t("maintenance.hideHistory") : t("maintenance.history")}
        </Button>
      </div>
      {noting ? (
        <div className="flex gap-2">
          <Input aria-label={t("maintenance.note")} placeholder={t("maintenance.notePh")} value={note} onChange={(e) => setNote(e.target.value)} />
          <Button
            type="button"
            size="sm"
            disabled={done.isPending}
            onClick={() =>
              done.mutate(
                { id: task.id, note: note.trim() || null },
                {
                  onSuccess: () => {
                    setNoting(false);
                    setNote("");
                  },
                },
              )
            }
          >
            {t("common.confirm")}
          </Button>
        </div>
      ) : null}
      {done.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {errText(done.error, t("maintenance.doneError"))}
        </p>
      ) : null}
      {showLog ? <LogList printerId={printerId} taskId={task.id} /> : null}
    </li>
  );
}

export function MaintenanceSection({
  printerId,
  maintenanceMode,
  canControl,
}: {
  printerId: number;
  maintenanceMode: boolean;
  canControl: boolean;
}) {
  const { t } = useTranslation();
  const tasks = useMaintenanceTasks(printerId);
  const mode = useSetMaintenanceMode(printerId);
  const [adding, setAdding] = useState(false);
  const [modeOn, setModeOn] = useState(maintenanceMode);
  const [confirmOn, setConfirmOn] = useState(false);
  function applyMode(v: boolean) {
    setModeOn(v);
    mode.mutate(v, { onError: () => setModeOn(!v) });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("maintenance.title")}</CardTitle>
        <CardDescription>{t("maintenance.desc")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-start gap-3">
          <Switch
            id="maint-mode"
            checked={modeOn}
            disabled={!canControl || mode.isPending}
            onCheckedChange={(v) => (v ? setConfirmOn(true) : applyMode(false))}
          />
          <ConfirmDialog
            open={confirmOn}
            onOpenChange={setConfirmOn}
            title={t("maintenance.modeTitle")}
            description={t("maintenance.modeConfirmDesc")}
            confirmLabel={t("maintenance.enable")}
            cancelLabel={t("common.cancel")}
            onConfirm={() => applyMode(true)}
          />
          <div>
            <Label htmlFor="maint-mode">{t("maintenance.mode")}</Label>
            <p className="text-xs text-muted-foreground">
              {t("maintenance.modeDesc")}
            </p>
          </div>
        </div>
        {mode.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {errText(mode.error, t("maintenance.modeError"))}
          </p>
        ) : null}
        {tasks.isLoading ? (
          <p className="text-sm text-muted-foreground">{t("maintenance.loading")}</p>
        ) : tasks.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {errText(tasks.error, t("maintenance.loadError"))}
          </p>
        ) : tasks.data?.length ? (
          <ul className="space-y-2">
            {tasks.data.map((task) => (
              <TaskRow key={task.id} task={task} printerId={printerId} canControl={canControl} />
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">{t("maintenance.none")}</p>
        )}
        {canControl ? (
          adding ? (
            <TaskForm printerId={printerId} onDone={() => setAdding(false)} />
          ) : (
            <Button type="button" size="sm" variant="outline" onClick={() => setAdding(true)}>
              {t("maintenance.addTask")}
            </Button>
          )
        ) : null}
      </CardContent>
    </Card>
  );
}
