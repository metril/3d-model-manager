/** Colour-catalog autocomplete (manufacturer + name search) for the spool form. */
import { useDeferredValue, useState } from "react";
import { useTranslation } from "react-i18next";

import { useColorSearch } from "@/api/spoolAdmin";
import type { ColorCatalogEntry } from "@/api/types";
import { FilamentChip } from "@/components/ui/filament-chip";
import { Input } from "@/components/ui/input";

export function ColorCatalogPicker({
  onPick,
  disabled,
}: {
  onPick: (entry: ColorCatalogEntry) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const deferred = useDeferredValue(q);
  const results = useColorSearch(deferred);
  const list = (results.data ?? []).slice(0, 12);

  return (
    <div className="relative">
      <Input
        aria-label={t("spoolAdmin.catalog.search")}
        placeholder={t("spoolAdmin.catalog.placeholder")}
        autoComplete="off"
        value={q}
        disabled={disabled}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
      />
      {open && q.trim().length >= 2 ? (
        <ul role="listbox" className="absolute z-20 mt-1 max-h-40 w-full overflow-y-auto rounded-md border bg-popover p-1 text-sm shadow-md">
          {list.length === 0 && !results.isFetching ? (
            <li className="px-2 py-1 text-muted-foreground">{t("spoolAdmin.catalog.none")}</li>
          ) : null}
          {list.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                role="option"
                aria-selected={false}
                className="flex w-full items-center gap-2 rounded px-2 py-1 text-left hover:bg-accent"
                onClick={() => {
                  onPick(c);
                  setQ("");
                  setOpen(false);
                }}
              >
                <FilamentChip color={c.hex} />
                <span>
                  {c.manufacturer} · {c.color_name}
                </span>
                {c.material ? <span className="ml-auto text-xs text-muted-foreground">{c.material}</span> : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
