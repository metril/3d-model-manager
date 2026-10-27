/**
 * One printer's dashboard card (Q3, bambuddy layout). Composition only: the
 * sections live in `card/` (`CardHeader`, `BadgeRow`, `StatusSection`,
 * `TempFanTiles`, `ControlsSection`, `FilamentsSection`, `CardFooter`).
 *
 * Sizes S/M/L/XL (`size`): S is a compact card (click expands it to M with a
 * "Back" pill); L/XL scale the body via `--pc-s`. Slots for sibling phases:
 *   - `headerExtra` / `badgeExtra` / `footerExtra`: node or `(ctx) => node`
 *     (filled by `usePrinterCardExtras`: maintenance/detection badges,
 *     plug row, camera split button, firmware + history buttons). The compact
 *     S card renders `badgeExtra` only.
 *   - `onHms`: `usePrinterModals().open(...)` (Info/Diagnose live in the toolbar menu).
 *   - `camera`: optional live tile above the header; `badge`: top-right overlay.
 * A sliced file dropped on the card is uploaded and queued for this printer.
 */
import { ArrowLeftIcon, ChevronRightIcon } from "lucide-react";
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { PrinterOverviewItem, PrinterOut, PrinterStatusOut } from "@/api/types";
import { Checkbox } from "@/components/ui/checkbox";
import { UpNextList } from "@/components/print/UpNextList";
import { BadgeRow } from "@/components/print/card/BadgeRow";
import { CARD_SCALE, type CardSize } from "@/components/print/card/cardSize";
import { CardFooter } from "@/components/print/card/CardFooter";
import { CardHeader } from "@/components/print/card/CardHeader";
import { ControlsSection } from "@/components/print/card/ControlsSection";
import { FilamentsSection } from "@/components/print/card/FilamentsSection";
import { StatePill } from "@/components/print/card/StatePill";
import { StatusSection } from "@/components/print/card/StatusSection";
import { TONE_BORDER_T, fillStyle, trackStyle } from "@/components/print/card/tone";
import { TempFanTiles } from "@/components/print/card/TempFanTiles";
import { useDropToQueue } from "@/components/print/card/useDropToQueue";
import { formatEta } from "@/lib/formatEta";
import { modelKey, printerImage, toneForGcode } from "@/lib/printerState";
import { cn } from "@/lib/utils";

export interface CardSlotCtx {
  printer: PrinterOut;
  status: PrinterStatusOut;
  canControl: boolean;
  canPrint: boolean;
  size: CardSize;
}
export type CardSlot = ReactNode | ((ctx: CardSlotCtx) => ReactNode);

function renderSlot(slot: CardSlot | undefined, ctx: CardSlotCtx): ReactNode {
  return typeof slot === "function" ? slot(ctx) : slot;
}

export interface PrinterCardProps {
  item: PrinterOverviewItem;
  size?: CardSize;
  camera?: ReactNode;
  badge?: ReactNode;
  headerExtra?: CardSlot;
  badgeExtra?: CardSlot;
  footerExtra?: CardSlot;
  onHms?: () => void;
  /** Opens the drying dialog for an AMS unit id. */
  onDry?: (unitId: number) => void;
  /** AMS slot -> mapping pill text. */
  amsMapping?: Record<number, string>;
  /** Bulk-select checkbox (dashboard). */
  selected?: boolean;
  onSelectedChange?: (selected: boolean) => void;
}

function CompactCard({ item, onExpand, selected, onSelectedChange, badgeExtra }: { item: PrinterOverviewItem } & {
  onExpand: () => void;
  badgeExtra?: ReactNode;
  selected?: boolean;
  onSelectedChange?: (selected: boolean) => void;
}) {
  const { t } = useTranslation();
  const { printer, status } = item;
  const { tone, label } = toneForGcode(status.online, status.gcode_state);
  const pct = tone === "printing" || tone === "paused" ? Math.max(0, Math.min(100, status.mc_percent ?? 0)) : null;
  const remaining = formatEta(status.mc_remaining_time);
  return (
    <div
      data-slot="printer-card"
      data-size="S"
      data-tone={tone}
      className={cn(
        "flex items-center gap-2 overflow-hidden rounded-lg border border-t-[3px] bg-card p-2 text-card-foreground shadow-elevation-1",
        TONE_BORDER_T[tone],
        tone === "offline" && "opacity-70",
      )}
    >
      {onSelectedChange ? (
        <Checkbox checked={!!selected} onCheckedChange={(v) => onSelectedChange(v === true)} aria-label={t("card.select", { name: printer.name })} />
      ) : null}
      <button type="button" className="flex min-w-0 flex-1 items-center gap-2 text-left" onClick={onExpand} aria-label={t("card.expand", { name: printer.name })}>
        <img src={printerImage(modelKey(printer.model, printer.serial))} alt="" className={cn("size-9 shrink-0 object-contain", tone === "offline" && "grayscale")} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="min-w-0 flex-1 truncate text-sm font-semibold">{printer.name}</span>
            <StatePill tone={tone} label={label} />
            {badgeExtra ? <span className="inline-flex gap-1">{badgeExtra}</span> : null}
          </span>
          {pct != null ? (
            <>
              <span
                role="progressbar"
                aria-valuenow={pct}
                aria-valuemin={0}
                aria-valuemax={100}
                className="mt-1.5 block h-0.5 w-full overflow-hidden rounded-full"
                style={trackStyle(tone)}
              >
                <span className="block h-full rounded-full" style={fillStyle(tone, pct)} />
              </span>
              <span className="mt-0.5 block font-mono text-xs tabular-nums text-muted-foreground">
                {pct}%{remaining ? ` · ${remaining}` : ""}
              </span>
            </>
          ) : null}
        </span>
      </button>
    </div>
  );
}

export function PrinterCard({
  item,
  size = "M",
  camera,
  badge,
  headerExtra,
  badgeExtra,
  footerExtra,
  onHms,
  onDry,
  amsMapping,
  selected,
  onSelectedChange,
}: PrinterCardProps) {
  const { printer, status, active_job: activeJob, can_control: canControl, can_print: canPrint, up_next: upNext } = item;
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const drop = useDropToQueue(printer.id);

  useEffect(() => {
    if (expanded) rootRef.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }, [expanded]);
  useEffect(() => {
    if (size !== "S") setExpanded(false);
  }, [size]);

  if (size === "S" && !expanded) {
    return (
      <div ref={rootRef}>
        <CompactCard
          item={item}
          onExpand={() => setExpanded(true)}
          selected={selected}
          onSelectedChange={onSelectedChange}
          badgeExtra={renderSlot(badgeExtra, { printer: item.printer, status: item.status, canControl: item.can_control, canPrint: item.can_print, size: "S" })}
        />
      </div>
    );
  }

  const effective: CardSize = size === "S" ? "M" : size;
  const ctx: CardSlotCtx = { printer, status, canControl, canPrint, size: effective };
  const { tone } = toneForGcode(status.online, status.gcode_state);
  const style = { "--pc-s": CARD_SCALE[effective] } as CSSProperties;

  const footerSlot = renderSlot(footerExtra, ctx);
  return (
    <div
      ref={rootRef}
      data-slot="printer-card"
      data-size={effective}
      data-tone={tone}
      style={style}
      className={cn(
        "relative flex flex-col overflow-hidden rounded-lg border border-t-[3px] bg-card text-card-foreground shadow-elevation-1",
        TONE_BORDER_T[tone],
        tone === "failed" && "bg-[color-mix(in_oklab,var(--state-failed)_5%,var(--card))]",
        tone === "offline" && "opacity-70",
        dragOver && canPrint && "ring-2 ring-primary",
        selected && "ring-2 ring-primary/60",
      )}
      onDragOver={(e) => {
        if (!canPrint) return;
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        setDragOver(false);
        if (!canPrint) return;
        e.preventDefault();
        void drop(e.dataTransfer);
      }}
    >
      {camera ? <div className="relative aspect-video bg-muted">{camera}</div> : null}
      {badge != null ? <div className="absolute right-2 top-2 z-10">{badge}</div> : null}
      {size === "S" ? (
        <button
          type="button"
          onClick={() => setExpanded(false)}
          className="absolute right-12 top-2 z-10 inline-flex items-center gap-1 rounded-full border bg-background px-2 py-0.5 text-xs hover:bg-muted"
        >
          <ArrowLeftIcon className="size-3" />
          {t("card.back")}
        </button>
      ) : null}

      <div className="flex flex-col gap-3 p-3">
        <div className="flex items-start gap-2">
          {onSelectedChange ? (
            <Checkbox
              className="mt-3"
              checked={!!selected}
              onCheckedChange={(v) => onSelectedChange(v === true)}
              aria-label={t("card.select", { name: printer.name })}
            />
          ) : null}
          <div className="min-w-0 flex-1">
            <CardHeader
              printer={printer}
              status={status}
              extra={renderSlot(headerExtra, ctx)}
              imageClassName={effective === "XL" ? "size-12" : "size-10"}
            />
          </div>
        </div>
        <BadgeRow printer={printer} status={status} queueCount={upNext.length} onHms={onHms} extra={renderSlot(badgeExtra, ctx)} />
        <StatusSection
          printerId={printer.id}
          status={status}
          activeJob={activeJob}
          lastRuns={item.last_runs}
          canControl={canControl}
        />
        <CardFooter
          printerId={printer.id}
          canPrint={canPrint}
          canControl={canControl}
          online={status.online}
          gcodeState={status.gcode_state}
        />
        {status.online ? <TempFanTiles printer={printer} status={status} canControl={canControl} /> : null}
        <FilamentsSection printer={printer} status={status} canControl={canControl} mapping={amsMapping} onDry={onDry} />
        <div
          className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1.5 border-t pt-2 empty:hidden"
          data-slot="secondary-row"
        >
          <ControlsSection printer={printer} status={status} canControl={canControl} size={effective} />
          <div className="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1.5">{footerSlot}</div>
        </div>
        {upNext.length > 0 ? (
          <details className="group border-t pt-2" data-slot="up-next">
            <summary className="flex cursor-pointer list-none items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground">
              <ChevronRightIcon className="size-3.5 transition-transform group-open:rotate-90" />
              {t("card.upNext", { n: upNext.length })}
            </summary>
            <div className="pt-2">
              <UpNextList entries={upNext} printerId={printer.id} bare />
            </div>
          </details>
        ) : null}
      </div>
    </div>
  );
}
