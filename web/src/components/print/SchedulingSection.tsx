/**
 * T4: collapsible "Scheduling" section shared by the PrintDialog Send step
 * (and reused by the batch dialog / inline edit popover via
 * `SchedulingFields`). Collapsed by default; the summary line shows what is
 * set. The body mounts only when expanded, so its queries (batches, plugs)
 * stay idle for the common untouched case.
 */
import { ChevronDownIcon, ChevronRightIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useAuth } from "@/api/auth";
import { usePlugSummary } from "@/api/ops";
import { useBatches } from "@/api/queue";
import type { PrinterOut } from "@/api/types";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SwitchRow } from "@/components/ui/switch-row";
import { cn } from "@/lib/utils";
import { isScheduleSet, schedulingSummary, type BatchChoice, type ScheduleDraft } from "@/lib/schedule";

/** Segmented inherit | on | off preheat control + chamber target. */
function PreheatField({
  draft,
  onChange,
  hasChamber,
}: {
  draft: ScheduleDraft;
  onChange: (d: ScheduleDraft) => void;
  hasChamber: boolean;
}) {
  const { t } = useTranslation();
  const options = ["inherit", "on", "off"] as const;
  return (
    <div className="space-y-1.5">
      <Label>{t("queueSched.preheat")}</Label>
      <div role="radiogroup" aria-label={t("queueSched.preheat")} className="inline-flex rounded-md border border-border p-0.5">
        {options.map((o) => (
          <button
            key={o}
            type="button"
            role="radio"
            aria-checked={draft.preheat === o}
            onClick={() => onChange({ ...draft, preheat: o })}
            className={cn(
              "rounded px-3 py-1 text-xs",
              draft.preheat === o ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted",
            )}
          >
            {t(`queueSched.${o}`)}
          </button>
        ))}
      </div>
      {draft.preheat === "on" ? (
        <div className="flex items-center gap-2">
          <Label htmlFor="sched-chamber" className="font-normal">
            {hasChamber ? t("queueSched.chamberTarget") : t("queueSched.bedSoak")}
          </Label>
          <Input
            id="sched-chamber"
            type="number"
            min={0}
            max={65}
            className="w-20"
            value={draft.chamberTarget}
            onChange={(e) => onChange({ ...draft, chamberTarget: e.target.value })}
          />
        </div>
      ) : null}
    </div>
  );
}

/** The editable fields (no batch picker). `hasPlug`: true | false | null (unknown). */
export function SchedulingFields({
  draft,
  onChange,
  hasChamber = true,
  hasPlug = null,
  idPrefix = "sched",
}: {
  draft: ScheduleDraft;
  onChange: (d: ScheduleDraft) => void;
  hasChamber?: boolean;
  hasPlug?: boolean | null;
  idPrefix?: string;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Label htmlFor={`${idPrefix}-start`} className="font-normal">
          {t("queueSched.startAt")}
        </Label>
        <Input
          id={`${idPrefix}-start`}
          type="datetime-local"
          className="w-56"
          value={draft.startAt}
          onChange={(e) => onChange({ ...draft, startAt: e.target.value })}
        />
        <button
          type="button"
          className="text-xs text-muted-foreground underline disabled:opacity-40"
          disabled={!draft.startAt}
          onClick={() => onChange({ ...draft, startAt: "" })}
        >
          {t("queueSched.asap")}
        </button>
      </div>
      <SwitchRow
        label={t("queueSched.manualStart")}
        description={t("queueSched.manualStartHint")}
        checked={draft.manualStart}
        onCheckedChange={(v) => onChange({ ...draft, manualStart: v })}
      />
      <SwitchRow
        label={t("queueSched.requirePrev")}
        checked={draft.requirePrev}
        onCheckedChange={(v) => onChange({ ...draft, requirePrev: v })}
      />
      {hasPlug !== false ? (
        <SwitchRow
          label={t("queueSched.autoOff")}
          description={hasPlug === null ? t("queueSched.autoOffUnknown") : t("queueSched.autoOffHint")}
          checked={draft.autoOff}
          onCheckedChange={(v) => onChange({ ...draft, autoOff: v })}
        />
      ) : null}
      <PreheatField draft={draft} onChange={onChange} hasChamber={hasChamber} />
    </div>
  );
}

function BatchPicker({ value, onChange }: { value: BatchChoice; onChange: (b: BatchChoice) => void }) {
  const { t } = useTranslation();
  const batches = useBatches();
  const active = (batches.data ?? []).filter((b) => b.status === "draft" || b.status === "active");
  const selectValue = value.mode === null ? "none" : value.mode === "new" ? "new" : String(value.mode);
  return (
    <div className="space-y-2">
      <Label htmlFor="sched-batch">{t("queueSched.batch")}</Label>
      <select
        id="sched-batch"
        className="h-8 w-64 rounded-md border border-input bg-transparent px-2 text-sm"
        value={selectValue}
        onChange={(e) => {
          const v = e.target.value;
          onChange({ ...value, mode: v === "none" ? null : v === "new" ? "new" : Number(v) });
        }}
      >
        <option value="none">{t("queueSched.noBatch")}</option>
        {active.map((b) => (
          <option key={b.id} value={String(b.id)}>
            {b.name}
          </option>
        ))}
        <option value="new">{t("queueSched.newBatch")}</option>
      </select>
      {value.mode === "new" ? (
        <div className="flex flex-wrap items-center gap-2">
          <Input
            aria-label={t("queueSched.batchName")}
            placeholder={t("queueSched.batchName")}
            className="w-48"
            value={value.name}
            onChange={(e) => onChange({ ...value, name: e.target.value })}
          />
          <Input
            aria-label={t("queueSched.batchQty")}
            type="number"
            min={1}
            max={500}
            className="w-20"
            value={value.quantity}
            onChange={(e) => onChange({ ...value, quantity: Math.min(500, Math.max(1, Number(e.target.value) || 1)) })}
          />
        </div>
      ) : null}
    </div>
  );
}

function SectionBody({
  draft,
  onChange,
  batch,
  onBatchChange,
  printer,
}: {
  draft: ScheduleDraft;
  onChange: (d: ScheduleDraft) => void;
  batch: BatchChoice;
  onBatchChange: (b: BatchChoice) => void;
  printer: PrinterOut | null;
}) {
  const plugs = usePlugSummary();
  const { can } = useAuth();
  const hasPlug =
    printer == null || plugs.data == null ? null : Boolean(plugs.data.plugs?.[String(printer.id)]);
  return (
    <div className="space-y-4 pt-3">
      <SchedulingFields
        draft={draft}
        onChange={onChange}
        hasChamber={printer?.capabilities?.chamber_heater ?? true}
        hasPlug={hasPlug}
      />
      {can("queue:review") ? <BatchPicker value={batch} onChange={onBatchChange} /> : null}
    </div>
  );
}

export function SchedulingSection({
  open,
  onOpenChange,
  draft,
  onChange,
  batch,
  onBatchChange,
  printer,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  draft: ScheduleDraft;
  onChange: (d: ScheduleDraft) => void;
  batch: BatchChoice;
  onBatchChange: (b: BatchChoice) => void;
  printer: PrinterOut | null;
}) {
  const { t } = useTranslation();
  const set = isScheduleSet(draft) || batch.mode !== null;
  return (
    <div className="rounded-md border border-border px-3 py-2">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => onOpenChange(!open)}
        className="flex w-full items-center gap-2 text-left text-sm font-medium"
      >
        {open ? <ChevronDownIcon className="size-4" /> : <ChevronRightIcon className="size-4" />}
        {t("queueSched.section")}
        {!open ? (
          <span className={cn("truncate text-xs font-normal", set ? "text-foreground" : "text-muted-foreground")}>
            {schedulingSummary(draft, batch, t as never)}
          </span>
        ) : null}
      </button>
      {open ? (
        <SectionBody draft={draft} onChange={onChange} batch={batch} onBatchChange={onBatchChange} printer={printer} />
      ) : null}
    </div>
  );
}
