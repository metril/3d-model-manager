/** Tray select for the Filament step: loaded AMS spools first, empty slots
 * behind a "Show all trays" toggle (the current choice is always listed). */
import { useState } from "react";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export interface TrayChoice {
  slot: number;
  label: string;
  loaded: boolean;
}

export function TrayPicker({
  trays,
  selected,
  onChange,
  ariaLabel,
}: {
  trays: TrayChoice[];
  selected: number | null;
  onChange: (slot: number) => void;
  ariaLabel: string;
}) {
  const [showAll, setShowAll] = useState(false);
  const loaded = trays.filter((t) => t.loaded || t.slot === selected);
  const hidden = trays.length - loaded.length;
  const shown = showAll || loaded.length === 0 ? trays : loaded;
  return (
    <>
      <Select
        value={selected != null ? String(selected) : ""}
        onValueChange={(v) => onChange(Number(v))}
        aria-label={ariaLabel}
      >
        <SelectTrigger className="h-8 w-36">
          <SelectValue placeholder="Choose tray" />
        </SelectTrigger>
        <SelectContent>
          {shown.map((t) => (
            <SelectItem key={t.slot} value={String(t.slot)}>
              {t.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {hidden > 0 || (showAll && loaded.length < trays.length) ? (
        <button
          type="button"
          className="mt-1 block text-xs text-muted-foreground underline"
          onClick={() => setShowAll((v) => !v)}
        >
          {showAll ? "Loaded spools only" : `Show all trays (${hidden} empty)`}
        </button>
      ) : null}
    </>
  );
}
