/**
 * Add/edit dialog for one Home Assistant `ha_links` row (P8 Task 8):
 * entity, printer target ("All printers" = `printer_id: null`), role, an
 * optional alert condition (op + value), and the hold-queue/pause-print
 * switches. Role presets: switching to `door`/`smoke` seeds a brand-new
 * condition to `eq "on"` -- there's otherwise nothing for a freshly-added
 * binary alert to fire on. Client-side mirrors the backend's two
 * invariants (`check_link_invariants`) so a bad combination never round-
 * trips to a 422: `energy_kwh` needs a specific printer, and
 * hold_queue/pause_print needs a condition.
 */
import { useEffect, useState } from "react";

import { ApiError } from "@/api/client";
import { useSaveHaLink } from "@/api/homeAssistant";
import { usePrinters } from "@/api/printers";
import type { HaCondition, HaConditionOp, HaLink, HaRole } from "@/api/types";
import { Button } from "@/components/ui/button";
import { FormDialog } from "@/components/ui/form-dialog";
import {
  DialogClose,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

const ROLE_OPTIONS: { value: HaRole; label: string }[] = [
  { value: "door", label: "Door" },
  { value: "smoke", label: "Smoke" },
  { value: "temp", label: "Temperature" },
  { value: "humidity", label: "Humidity" },
  { value: "power_w", label: "Power (W)" },
  { value: "energy_kwh", label: "Energy (kWh)" },
];

const OP_OPTIONS: HaConditionOp[] = ["eq", "ne", "gt", "gte", "lt", "lte", "in"];
const NUMERIC_OPS: HaConditionOp[] = ["gt", "gte", "lt", "lte"];

// Role presets (P8 Task 8 brief): door/smoke default to `eq "on"`.
const ALERT_DEFAULT_ROLES: HaRole[] = ["door", "smoke"];

const ALL_PRINTERS = "all" as const;

function errText(e: unknown, fallback: string) {
  return e instanceof ApiError ? e.detail : fallback;
}

function conditionValueToString(cond: HaCondition | null): string {
  if (!cond) return "on";
  return Array.isArray(cond.value) ? cond.value.join(",") : String(cond.value);
}

export function HaLinkDialog({
  open,
  link,
  onOpenChange,
}: {
  open: boolean;
  link: HaLink | null;
  onOpenChange: (open: boolean) => void;
}) {
  const printers = usePrinters();
  const save = useSaveHaLink();
  const isEdit = link !== null;

  const [entityId, setEntityId] = useState("");
  const [printerId, setPrinterId] = useState<string>(ALL_PRINTERS);
  const [role, setRole] = useState<HaRole>("door");
  const [hasCondition, setHasCondition] = useState(false);
  const [op, setOp] = useState<HaConditionOp>("eq");
  const [value, setValue] = useState("on");
  const [holdQueue, setHoldQueue] = useState(false);
  const [pausePrint, setPausePrint] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  // Reset the form whenever the dialog (re)opens -- for a fresh "Add" or
  // for a different row's "Edit".
  useEffect(() => {
    if (!open) return;
    setEntityId(link?.entity_id ?? "");
    setPrinterId(link?.printer_id != null ? String(link.printer_id) : ALL_PRINTERS);
    setRole(link?.role ?? "door");
    setHasCondition(link?.alert_condition != null);
    setOp(link?.alert_condition?.op ?? "eq");
    setValue(conditionValueToString(link?.alert_condition ?? null));
    setHoldQueue(link?.hold_queue ?? false);
    setPausePrint(link?.pause_print ?? false);
    setLocalError(null);
    save.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, link]);

  const initialSnap = JSON.stringify([
    link?.entity_id ?? "",
    link?.printer_id != null ? String(link.printer_id) : ALL_PRINTERS,
    link?.role ?? "door",
    link?.alert_condition != null,
    link?.alert_condition?.op ?? "eq",
    conditionValueToString(link?.alert_condition ?? null),
    link?.hold_queue ?? false,
    link?.pause_print ?? false,
  ]);
  const dirty = JSON.stringify([entityId, printerId, role, hasCondition, op, value, holdQueue, pausePrint]) !== initialSnap;

  function selectRole(next: HaRole) {
    setRole(next);
    if (ALERT_DEFAULT_ROLES.includes(next) && !hasCondition) {
      setOp("eq");
      setValue("on");
      setHasCondition(true);
    }
  }

  function buildCondition(): HaCondition | null {
    if (!hasCondition) return null;
    if (op === "in") {
      return { op, value: value.split(",").map((v) => v.trim()).filter(Boolean) };
    }
    if (NUMERIC_OPS.includes(op)) {
      return { op, value: Number(value) };
    }
    return { op, value };
  }

  function submit() {
    setLocalError(null);
    if (!entityId.trim()) {
      setLocalError("Entity ID is required.");
      return;
    }
    if (role === "energy_kwh" && printerId === ALL_PRINTERS) {
      setLocalError('Role "Energy (kWh)" requires a specific printer.');
      return;
    }
    const condition = buildCondition();
    if ((holdQueue || pausePrint) && condition === null) {
      setLocalError("Hold queue / pause print needs an alert condition to fire on.");
      return;
    }
    const body = {
      printer_id: printerId === ALL_PRINTERS ? null : Number(printerId),
      entity_id: entityId.trim(),
      role,
      alert_condition: condition,
      hold_queue: holdQueue,
      pause_print: pausePrint,
    };
    save.mutate({ id: link?.id, body }, { onSuccess: () => onOpenChange(false) });
  }

  return (
    <FormDialog open={open} onOpenChange={onOpenChange} dirty={dirty} busy={save.isPending}>
        <DialogHeader>
          <DialogTitle>{isEdit ? `Edit ${link.entity_id}` : "Add linked entity"}</DialogTitle>
          <DialogDescription>Wire a Home Assistant entity to the printer dispatcher.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ha-link-entity">Entity ID</Label>
            <Input
              id="ha-link-entity"
              value={entityId}
              onChange={(e) => setEntityId(e.target.value)}
              placeholder="binary_sensor.front_door"
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="ha-link-printer">Printer</Label>
              <Select aria-label="Printer" value={printerId} onValueChange={setPrinterId}>
                <SelectTrigger id="ha-link-printer" aria-label="Printer" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL_PRINTERS}>All printers</SelectItem>
                  {(printers.data ?? []).map((p) => (
                    <SelectItem key={p.id} value={String(p.id)}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="ha-link-role">Role</Label>
              <Select aria-label="Role" value={role} onValueChange={(v) => selectRole(v as HaRole)}>
                <SelectTrigger id="ha-link-role" aria-label="Role" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ROLE_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <label className="flex items-center gap-2 text-sm">
            <Switch checked={hasCondition} onCheckedChange={setHasCondition} aria-label="Has alert condition" />
            Alert condition
          </label>
          {hasCondition ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="ha-link-op">Condition</Label>
                <Select aria-label="Condition" value={op} onValueChange={(v) => setOp(v as HaConditionOp)}>
                  <SelectTrigger id="ha-link-op" aria-label="Condition" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {OP_OPTIONS.map((o) => (
                      <SelectItem key={o} value={o}>
                        {o}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="ha-link-value">Value</Label>
                <Input id="ha-link-value" value={value} onChange={(e) => setValue(e.target.value)} />
              </div>
            </div>
          ) : null}

          <div className="flex flex-col gap-2">
            <label className="flex items-center gap-2 text-sm">
              <Switch checked={holdQueue} onCheckedChange={setHoldQueue} aria-label="Hold queue" />
              Hold queue
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Switch checked={pausePrint} onCheckedChange={setPausePrint} aria-label="Pause print" />
              Pause print
            </label>
            <p className="text-xs text-muted-foreground">
              Off by default — pauses a running print when the alert fires.
            </p>
          </div>

          {(localError || save.isError) && (
            <p role="alert" className="text-sm text-destructive">
              {localError ?? errText(save.error, "Could not save the link.")}
            </p>
          )}
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button type="button" variant="outline">
            Cancel
          </Button>
          </DialogClose>
          <Button type="button" disabled={save.isPending} onClick={submit}>
            {isEdit ? "Save" : "Add link"}
          </Button>
        </DialogFooter>
    </FormDialog>
  );
}
