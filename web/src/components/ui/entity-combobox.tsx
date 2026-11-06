/** Searchable picker for Home Assistant entities (and notify services). Every
 * option shows the friendly name, the `entity_id`, then state + unit. Typed
 * text that matches no option is still accepted, so the field keeps working
 * while HA is down. Renders through the Popover portal, so it works inside
 * dialogs and dock panels. */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { CheckIcon, ChevronsUpDownIcon } from "lucide-react";

import { useHaPickerEntities, useHaServices, type HaEntityFilter } from "@/api/haPicker";
import type { HaEntity } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

interface Option {
  value: string;
  label: string | null;
  detail: string | null;
}

interface BaseProps {
  value: string;
  onChange: (v: string) => void;
  id?: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  ariaLabel?: string;
}

function ComboboxBase({
  value,
  onChange,
  options,
  loading,
  error,
  id,
  placeholder,
  disabled,
  className,
  ariaLabel,
}: BaseProps & { options: Option[]; loading: boolean; error: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const typed = text.trim();
  const exact = options.some((o) => o.value === typed);

  function pick(v: string) {
    onChange(v);
    setOpen(false);
  }

  return (
    <Popover
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (o) setText("");
      }}
    >
      <PopoverTrigger asChild>
        <Button
          id={id}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-label={ariaLabel}
          disabled={disabled}
          className={cn("w-full justify-between font-normal", className)}
        >
          <span className={cn("truncate font-mono text-xs", !value && "text-muted-foreground")}>
            {value || placeholder || t("haPicker.choose")}
          </span>
          <ChevronsUpDownIcon className="size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-(--radix-popover-trigger-width) min-w-72 p-0">
        <Command>
          <CommandInput value={text} onValueChange={setText} placeholder={t("haPicker.search")} />
          <CommandList>
            <CommandEmpty>
              {loading ? t("haPicker.loading") : error ? t("haPicker.unavailable") : t("haPicker.none")}
            </CommandEmpty>
            <CommandGroup>
              {options.map((o) => (
                <CommandItem
                  key={o.value}
                  value={`${o.value} ${o.label ?? ""}`}
                  onSelect={() => pick(o.value)}
                  className="items-start"
                >
                  <CheckIcon className={cn("mt-0.5 size-4 shrink-0", value === o.value ? "opacity-100" : "opacity-0")} />
                  <span className="flex min-w-0 flex-col">
                    <span className="truncate">{o.label ?? o.value}</span>
                    {o.label ? <span className="truncate font-mono text-xs text-muted-foreground">{o.value}</span> : null}
                    {o.detail ? <span className="truncate text-xs text-muted-foreground">{o.detail}</span> : null}
                  </span>
                </CommandItem>
              ))}
              {typed && !exact ? (
                <CommandItem forceMount value={`__use__ ${typed}`} onSelect={() => pick(typed)}>
                  {t("haPicker.use", { value: typed })}
                </CommandItem>
              ) : null}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

function entityOption(e: HaEntity): Option {
  const state = e.state != null ? `${e.state}${e.unit ? ` ${e.unit}` : ""}` : null;
  return { value: e.entity_id, label: e.friendly_name, detail: state };
}

export interface EntityComboboxProps extends BaseProps, HaEntityFilter {
  /** Extra client-side filter on top of the server-side domain/class filter. */
  filter?: (e: HaEntity) => boolean;
}

export function EntityCombobox({ filter, domain, deviceClass, ...rest }: EntityComboboxProps) {
  const q = useHaPickerEntities({ domain, deviceClass });
  const rows = filter ? (q.data ?? []).filter(filter) : (q.data ?? []);
  return <ComboboxBase {...rest} options={rows.map(entityOption)} loading={q.isLoading} error={q.isError} />;
}

/** Combobox over `domain.service` names, e.g. the notify services. */
export function ServiceCombobox({ domain, ...rest }: BaseProps & { domain: string }) {
  const q = useHaServices(domain);
  const options = (q.data ?? []).map((s) => ({ value: s, label: null, detail: null }));
  return <ComboboxBase {...rest} options={options} loading={q.isLoading} error={q.isError} />;
}
