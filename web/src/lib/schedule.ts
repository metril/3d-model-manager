/** T4 scheduling helpers: `datetime-local` <-> ISO UTC, and the draft shape
 * the PrintDialog / batch dialog / inline popover share. */
import type { PreheatOverride, QueueSchedulingFields } from "@/api/types";

export interface ScheduleDraft {
  /** `datetime-local` value ("YYYY-MM-DDTHH:mm") in the viewer's zone; "" = ASAP. */
  startAt: string;
  manualStart: boolean;
  requirePrev: boolean;
  autoOff: boolean;
  preheat: PreheatOverride | "inherit";
  chamberTarget: string;
}

export const EMPTY_SCHEDULE: ScheduleDraft = {
  startAt: "",
  manualStart: false,
  requirePrev: false,
  autoOff: false,
  preheat: "inherit",
  chamberTarget: "",
};

/** "2026-10-24T14:30" (local) -> ISO UTC string, or null when blank/invalid. */
export function localToIso(local: string): string | null {
  if (!local) return null;
  const d = new Date(local);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

const pad = (n: number) => String(n).padStart(2, "0");

/** ISO (any zone) -> `datetime-local` value in the viewer's zone. */
export function isoToLocal(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function chamberTargetValue(draft: ScheduleDraft): number | null {
  if (draft.preheat !== "on" || draft.chamberTarget.trim() === "") return null;
  const n = Math.round(Number(draft.chamberTarget));
  return Number.isFinite(n) ? Math.min(65, Math.max(0, n)) : null;
}

/** Only fields the person actually set -- an untouched draft yields `{}`. */
export function draftToFields(draft: ScheduleDraft): QueueSchedulingFields {
  const out: QueueSchedulingFields = {};
  const iso = localToIso(draft.startAt);
  if (iso) out.scheduled_at = iso;
  if (draft.manualStart) out.manual_start = true;
  if (draft.requirePrev) out.require_previous_success = true;
  if (draft.autoOff) out.auto_off_after = true;
  if (draft.preheat !== "inherit") {
    out.preheat_override = draft.preheat;
    const target = chamberTargetValue(draft);
    if (target != null) out.preheat_chamber_target = target;
  }
  return out;
}

export function isScheduleSet(draft: ScheduleDraft): boolean {
  return Object.keys(draftToFields(draft)).length > 0;
}

/** Full PATCH body from a draft: unset fields are sent as explicit clears. */
export function draftToPatch(draft: ScheduleDraft): Omit<QueueSchedulingFields, "batch_id"> {
  return {
    scheduled_at: localToIso(draft.startAt),
    manual_start: draft.manualStart,
    require_previous_success: draft.requirePrev,
    auto_off_after: draft.autoOff,
    preheat_override: draft.preheat === "inherit" ? null : draft.preheat,
    preheat_chamber_target: chamberTargetValue(draft),
  };
}
