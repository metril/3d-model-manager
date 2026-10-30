import { PrinterIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { BulkPrinterToolbar } from "@/components/print/BulkPrinterToolbar";
import { CameraTile } from "@/components/print/CameraTile";
import { CARD_SIZES, gridStyle } from "@/components/print/card/cardSize";
import { HaAlertBadge } from "@/components/print/HaAlertBadge";
import { ManagedPrinterCard } from "@/components/print/ManagedPrinterCard";
import { PlateStallBadge } from "@/components/print/PlateStallBadge";
import { StatusSummaryBar } from "@/components/print/StatusSummaryBar";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { DASH_SORTS, type PrintDashSearch } from "@/pages/printerDashSearch";
import { usePrintDash } from "./PrintDashContext";

/** The dashboard's main column: fleet summary, filter/sort toolbar, bulk
 * select and the grouped printer-card grid. */
export function PrintersPanel() {
  const { t } = useTranslation();
  const { printers, groups, summary, search, patchSearch, size, setSize, refetch } = usePrintDash();
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <StatusSummaryBar summary={summary} active={search.status} onSelect={(status) => patchSearch({ status })} />

      <div className="flex flex-wrap items-center gap-2" data-slot="dash-toolbar">
        <Input
          type="search"
          className="h-8 w-48"
          placeholder="Search printers"
          aria-label="Search printers"
          defaultValue={search.q ?? ""}
          onChange={(e) => patchSearch({ q: e.target.value || undefined })}
        />
        <label className="flex items-center gap-1.5 text-sm">
          <input
            type="checkbox"
            checked={search.offline === "hide"}
            onChange={(e) => patchSearch({ offline: e.target.checked ? "hide" : undefined })}
          />
          Hide offline
        </label>
        <label className="flex items-center gap-1.5 text-sm">
          Sort
          <select
            aria-label="Sort printers"
            className="h-8 rounded-md border bg-card px-2 text-sm"
            value={search.sort ?? "name"}
            onChange={(e) => patchSearch({ sort: e.target.value === "name" ? undefined : (e.target.value as PrintDashSearch["sort"]) })}
          >
            {DASH_SORTS.map((s) => (
              <option key={s} value={s}>
                {s === "eta" ? "ETA" : s[0].toUpperCase() + s.slice(1)}
              </option>
            ))}
          </select>
        </label>
        <Button
          type="button"
          size="sm"
          variant={selectMode ? "default" : "outline"}
          aria-pressed={selectMode}
          onClick={() => {
            setSelectMode((v) => !v);
            setSelected(new Set());
          }}
        >
          Select
        </Button>
        <div role="radiogroup" aria-label="Card size" className="ml-auto inline-flex overflow-hidden rounded-md border">
          {CARD_SIZES.map((s) => (
            <button
              key={s}
              type="button"
              role="radio"
              aria-checked={size === s}
              className={cn("px-2.5 py-1 text-xs", size === s ? "bg-primary text-primary-foreground" : "hover:bg-muted")}
              onClick={() => setSize(s)}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {selectMode && selected.size > 0 ? (
        <BulkPrinterToolbar
          selected={printers.filter((p) => selected.has(p.printer.id))}
          onDone={() => {
            setSelected(new Set());
            refetch();
          }}
          onClear={() => setSelected(new Set())}
        />
      ) : null}

      {groups.every((g) => g.items.length === 0) ? (
        <EmptyState
          icon={PrinterIcon}
          title={t("dash.empty.filters")}
          action={
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => patchSearch({ status: undefined, offline: undefined, q: undefined })}
            >
              {t("dash.empty.clearFilters")}
            </Button>
          }
        />
      ) : null}
      {groups.map((group) => (
        <section key={group.key} className="flex flex-col gap-2">
          {group.label ? (
            <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {group.label} <span className="font-mono">({group.items.length})</span>
            </h2>
          ) : null}
          <div className={cn("grid gap-4", size === "L" || size === "XL" ? "gap-6" : "")} style={gridStyle(size)}>
            {group.items.map((item) => (
              <ManagedPrinterCard
                key={item.printer.id}
                item={item}
                size={size}
                selected={selectMode ? selected.has(item.printer.id) : undefined}
                onSelectedChange={
                  selectMode
                    ? (on) =>
                        setSelected((prev) => {
                          const next = new Set(prev);
                          if (on) next.add(item.printer.id);
                          else next.delete(item.printer.id);
                          return next;
                        })
                    : undefined
                }
                camera={
                  item.can_print && (size === "L" || size === "XL") ? (
                    <CameraTile printerId={item.printer.id} online={item.status.online} />
                  ) : undefined
                }
                badge={
                  item.ha_alert || item.plate_check_stalled ? (
                    <>
                      {item.ha_alert ? <HaAlertBadge reason={item.ha_alert} held={item.ha_held ?? false} /> : null}
                      {item.plate_check_stalled ? <PlateStallBadge /> : null}
                    </>
                  ) : undefined
                }
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
