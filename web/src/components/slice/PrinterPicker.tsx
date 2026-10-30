/**
 * Printer radio list for the Slice and Print dialogs: online printers only,
 * the rest behind an "Offline (N)" disclosure, and the single online printer
 * is selected for you. Liveness comes from the printers overview poll; until
 * it loads every printer counts as online so nothing is hidden early.
 */
import { useEffect, useMemo } from "react";

import { usePrintersOverview } from "@/api/printers";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { splitOnline } from "@/lib/presetList";

export interface PickerPrinter {
  id: number;
  name: string;
  disabled?: boolean;
  reason?: string | null;
}

/** Ids of printers whose status reports online; `null` until known. */
function useOnlinePrinterIds(): ReadonlySet<number> | null {
  const overview = usePrintersOverview();
  const items = overview.data?.printers;
  return useMemo(
    () => (Array.isArray(items) ? new Set(items.filter((i) => i.status?.online).map((i) => i.printer.id)) : null),
    [items],
  );
}

export function PrinterPicker({
  printers,
  value,
  onChange,
  idPrefix,
}: {
  printers: PickerPrinter[];
  value: number | null;
  /** `auto` is true when the picker chose the sole online printer itself. */
  onChange: (id: number, auto: boolean) => void;
  idPrefix: string;
}) {
  const onlineIds = useOnlinePrinterIds();
  const { online, offline } = useMemo(() => splitOnline(printers, onlineIds), [printers, onlineIds]);
  const soleId = online.length === 1 && !online[0].disabled ? online[0].id : null;

  useEffect(() => {
    if (value == null && soleId != null) onChange(soleId, true);
  }, [value, soleId, onChange]);

  const row = (p: PickerPrinter) => (
    <div key={p.id} className="flex items-center gap-2">
      <RadioGroupItem value={String(p.id)} id={`${idPrefix}-${p.id}`} disabled={p.disabled} />
      <Label htmlFor={`${idPrefix}-${p.id}`} className="font-normal">
        {p.name}
        {p.disabled && p.reason ? <span className="ml-1 text-xs text-muted-foreground">({p.reason})</span> : null}
      </Label>
    </div>
  );

  return (
    <RadioGroup value={value != null ? String(value) : ""} onValueChange={(v) => onChange(Number(v), false)}>
      {online.map(row)}
      {online.length === 0 && offline.length > 0 ? (
        <p className="text-sm text-muted-foreground">No printers are online.</p>
      ) : null}
      {offline.length > 0 ? (
        <details className="text-sm" open={value != null && offline.some((p) => p.id === value)}>
          <summary className="cursor-pointer text-muted-foreground">Offline ({offline.length})</summary>
          <div className="mt-1 space-y-1.5">{offline.map(row)}</div>
        </details>
      ) : null}
    </RadioGroup>
  );
}
