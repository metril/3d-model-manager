import { useState } from "react";
import { PencilIcon, Trash2Icon } from "lucide-react";

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

function remaining(t: MaintenanceTask): string {
  const parts: string[] = [];
  if (t.hours_remaining != null) {
    const h = Math.abs(t.hours_remaining).toFixed(1);
    parts.push(t.hours_remaining < 0 ? `${h} h overdue` : `${h} h left`);
  }
  if (t.days_remaining != null) {
    const d = Math.abs(t.days_remaining);
    parts.push(t.days_remaining < 0 ? `${d} d overdue` : `${d} d left`);
  }
  return parts.length ? parts.join(" / ") : "No interval";
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
  const save = useSaveMaintenanceTask(printerId);
  const [name, setName] = useState(task?.name ?? "");
  const [hours, setHours] = useState(task?.interval_hours != null ? String(task.interval_hours) : "");
  const [days, setDays] = useState(task?.interval_days != null ? String(task.interval_days) : "");
  const [notes, setNotes] = useState(task?.notes ?? "");
  const [err, setErr] = useState<string | null>(null);
  const id = task ? `mt-${task.id}` : "mt-new";

  function submit() {
    if (!name.trim()) return setErr("Name is required.");
    if (!hours.trim() && !days.trim()) return setErr("Set an interval in hours or days.");
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
      { onSuccess: onDone, onError: (e) => setErr(errText(e, "Could not save task")) },
    );
  }

  return (
    <div className="grid gap-3 rounded-lg border p-3 sm:grid-cols-3">
      <div className="space-y-1.5 sm:col-span-3">
        <Label htmlFor={`${id}-name`}>Task name</Label>
        <Input id={`${id}-name`} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={`${id}-hours`}>Every (print hours)</Label>
        <Input id={`${id}-hours`} type="number" min="0" value={hours} onChange={(e) => setHours(e.target.value)} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={`${id}-days`}>Every (days)</Label>
        <Input id={`${id}-days`} type="number" min="0" value={days} onChange={(e) => setDays(e.target.value)} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={`${id}-notes`}>Notes</Label>
        <Input id={`${id}-notes`} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </div>
      {err ? (
        <p role="alert" className="text-sm text-destructive sm:col-span-3">
          {err}
        </p>
      ) : null}
      <div className="flex gap-2 sm:col-span-3">
        <Button type="button" size="sm" onClick={submit} disabled={save.isPending}>
          {task ? "Save task" : "Add task"}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function LogList({ printerId, taskId }: { printerId: number; taskId: number }) {
  const logs = useMaintenanceLogs(printerId, taskId);
  if (logs.isLoading) return <p className="text-xs text-muted-foreground">Loading history...</p>;
  if (!logs.data?.length) return <p className="text-xs text-muted-foreground">No history yet.</p>;
  return (
    <ul className="space-y-1 text-xs text-muted-foreground">
      {logs.data.map((l) => (
        <li key={l.id}>
          {new Date(l.done_at).toLocaleDateString()}
          {l.hours_at != null ? ` at ${l.hours_at.toFixed(1)} h` : ""}
          {l.note ? ` — ${l.note}` : ""}
        </li>
      ))}
    </ul>
  );
}

function TaskRow({ task, printerId, canControl }: { task: MaintenanceTask; printerId: number; canControl: boolean }) {
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
            {remaining(task)}
          </p>
        </div>
        {canControl ? (
          <>
            <Button type="button" size="sm" onClick={() => setNoting(true)}>
              Mark done
            </Button>
            <Button type="button" size="icon-sm" variant="ghost" aria-label={`Edit ${task.name}`} onClick={() => setEditing(true)}>
              <PencilIcon className="size-3.5" />
            </Button>
            <ConfirmDialog
              trigger={
                <Button type="button" size="icon-sm" variant="ghost" aria-label={`Delete ${task.name}`}>
                  <Trash2Icon className="size-3.5" />
                </Button>
              }
              title={`Delete ${task.name}?`}
              description="Its history is removed too."
              confirmLabel="Delete"
              destructive
              onConfirm={() => del.mutate(task.id)}
            />
          </>
        ) : null}
        <Button type="button" size="sm" variant="ghost" onClick={() => setShowLog((v) => !v)}>
          {showLog ? "Hide history" : "History"}
        </Button>
      </div>
      {noting ? (
        <div className="flex gap-2">
          <Input aria-label="Note" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
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
            Confirm
          </Button>
        </div>
      ) : null}
      {done.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {errText(done.error, "Could not mark done")}
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
        <CardTitle>Maintenance</CardTitle>
        <CardDescription>Service tasks, due by print hours or days.</CardDescription>
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
            title="Enable maintenance mode?"
            description="The dispatcher will skip this printer and won't start queued prints on it."
            confirmLabel="Enable"
            onConfirm={() => applyMode(true)}
          />
          <div>
            <Label htmlFor="maint-mode">Maintenance mode</Label>
            <p className="text-xs text-muted-foreground">
              While on, the dispatcher skips this printer and won&apos;t start queued prints on it.
            </p>
          </div>
        </div>
        {mode.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {errText(mode.error, "Could not change maintenance mode")}
          </p>
        ) : null}
        {tasks.isLoading ? (
          <p className="text-sm text-muted-foreground">Loading...</p>
        ) : tasks.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {errText(tasks.error, "Couldn't load maintenance tasks")}
          </p>
        ) : tasks.data?.length ? (
          <ul className="space-y-2">
            {tasks.data.map((t) => (
              <TaskRow key={t.id} task={t} printerId={printerId} canControl={canControl} />
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No maintenance tasks yet.</p>
        )}
        {canControl ? (
          adding ? (
            <TaskForm printerId={printerId} onDone={() => setAdding(false)} />
          ) : (
            <Button type="button" size="sm" variant="outline" onClick={() => setAdding(true)}>
              Add task
            </Button>
          )
        ) : null}
      </CardContent>
    </Card>
  );
}
