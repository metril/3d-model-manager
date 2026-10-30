/** Location picker for the spool form: search existing locations or create one inline. */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import { useCreateLocation, useLocations } from "@/api/spoolAdmin";
import { LocationClimate } from "@/components/inventory/LocationClimate";
import { Input } from "@/components/ui/input";

export function LocationCombobox({
  id = "spool-location",
  initialName,
  onChange,
  disabled,
}: {
  id?: string;
  initialName?: string | null;
  onChange: (locationId: number | null) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const locations = useLocations();
  const create = useCreateLocation();
  const [query, setQuery] = useState(initialName ?? "");
  const [open, setOpen] = useState(false);
  const [pickedName, setPickedName] = useState<string | null>(initialName ?? null);

  const term = query.trim();
  const all = locations.data ?? [];
  const matches = all.filter((l) => l.name.toLowerCase().includes(term.toLowerCase()));
  const exact = all.some((l) => l.name.toLowerCase() === term.toLowerCase());

  function pick(locId: number, name: string) {
    setQuery(name);
    setPickedName(name);
    setOpen(false);
    onChange(locId);
  }

  function createInline() {
    create.mutate({ name: term }, { onSuccess: (loc) => pick(loc.id, loc.name) });
  }

  return (
    <div className="relative">
      <Input
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={`${id}-list`}
        autoComplete="off"
        placeholder={t("spoolAdmin.loc.placeholder")}
        value={query}
        disabled={disabled}
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
          if (e.target.value.trim() !== pickedName?.trim()) {
            setPickedName(null);
            onChange(null);
          }
        }}
      />
      {!pickedName && term && !open ? (
        <p className="mt-1 text-xs text-muted-foreground">{t("spoolAdmin.loc.unassignedHint")}</p>
      ) : null}
      {open && !disabled ? (
        <ul
          id={`${id}-list`}
          role="listbox"
          className="absolute z-20 mt-1 max-h-48 w-full overflow-y-auto rounded-md border bg-popover p-1 text-sm shadow-md"
        >
          {matches.map((l) => (
            <li key={l.id}>
              <button
                type="button"
                role="option"
                aria-selected={false}
                className="w-full rounded px-2 py-1 text-left hover:bg-accent"
                onClick={() => pick(l.id, l.name)}
              >
                <span className="flex items-center justify-between gap-2">
                  {l.name}
                  <LocationClimate sensors={l.sensors} />
                </span>
              </button>
            </li>
          ))}
          {term && !exact ? (
            <li>
              <button
                type="button"
                role="option"
                aria-selected={false}
                disabled={create.isPending}
                className="w-full rounded px-2 py-1 text-left hover:bg-accent"
                onClick={createInline}
              >
                {t("spoolAdmin.loc.create", { name: term })}
              </button>
            </li>
          ) : null}
          {matches.length === 0 && !term ? (
            <li className="px-2 py-1 text-muted-foreground">{t("spoolAdmin.loc.empty")}</li>
          ) : null}
          {create.isError ? (
            <li role="alert" className="px-2 py-1 text-destructive">
              {create.error instanceof ApiError ? create.error.detail : t("spoolAdmin.loc.createFailed")}
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}
