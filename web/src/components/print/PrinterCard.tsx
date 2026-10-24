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
 *   - `onInfo` / `onDiagnose` / `onHms`: `usePrinterModals().open(...)`.
 *   - `camera`: optional live tile above the header; `badge`: top-right overlay.
 * A sliced file dropped on the card is uploaded and queued for this printer.
 */
import { ArrowLeftIcon } from "lucide-react";
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { PrinterOverviewItem, PrinterOut, PrinterStatusOut } from "@/api/types";
import { Checkbox } from "@/components/ui/checkbox";
import { StateBadge } from "@/components/ui/state-badge";
import { UpNextList } from "@/components/print/UpNextList";
import { BadgeRow } from "@/components/print/card/BadgeRow";
import { CARD_SCALE, type CardSize } from "@/components/print/card/cardSize";
import { CardFooter } from "@/components/print/card/CardFooter";
import { CardHeader } from "@/components/print/card/CardHeader";
import { ControlsSection } from "@/components/print/card/ControlsSection";
import { FilamentsSection } from "@/components/print/card/FilamentsSection";
import { StatusSection } from "@/components/print/card/StatusSection";
import { TempFanTiles } from "@/components/print/card/TempFanTiles";
import { useDropToQueue } from "@/components/print/card/useDropToQueue";
import { modelKey, printerImage, toneForGcode } from "@/lib/printerState";
import { cn } from "@/lib/utils";

export interface CardSlotCtx {
  printer: PrinterOut;
  status: PrinterStatusOut;
  canControl: boolean;
  canPrint: boolean;
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
  onInfo?: () => void;
  onDiagnose?: () => void;
  onHms?: () => void;
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
  return (
    <div
      data-slot="printer-card"
      data-size="S"
      className="flex items-center gap-2 overflow-hidden rounded-[6px] border p-2"
    >
      {onSelectedChange ? (
        <Checkbox checked={!!selected} onCheckedChange={(v) => onSelectedChange(v === true)} aria-label={t("card.select", { name: printer.name })} />
      ) : null}
      <button type="button" className="flex min-w-0 flex-1 items-center gap-2 text-left" onClick={onExpand} aria-label={t("card.expand", { name: printer.name })}>
        <img src={printerImage(modelKey(printer.model, printer.serial))} alt="" className="size-9 shrink-0 object-contain" />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">{printer.name}</span>
          <StateBadge tone={tone} label={label} pulse={tone === "printing"} />
          {badgeExtra ? <span className="ml-1 inline-flex gap-1 align-middle">{badgeExtra}</span> : null}
          {pct != null ? (
            <span
              role="progressbar"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
              className="mt-1 block h-1 w-full overflow-hidden rounded-full bg-muted"
            >
              <span className="block h-full rounded-full bg-state-printing" style={{ width: `${pct}%` }} />
            </span>
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
  onInfo,
  onDiagnose,
  onHms,
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
          badgeExtra={renderSlot(badgeExtra, { printer: item.printer, status: item.status, canControl: item.can_control, canPrint: item.can_print })}
        />
      </div>
    );
  }

  const effective: CardSize = size === "S" ? "M" : size;
  const ctx: CardSlotCtx = { printer, status, canControl, canPrint };
  const { tone, label } = toneForGcode(status.online, status.gcode_state);
  const style = { "--pc-s": CARD_SCALE[effective] } as CSSProperties;

  return (
    <div
      ref={rootRef}
      data-slot="printer-card"
      data-size={effective}
      style={style}
      className={cn(
        "relative flex flex-col overflow-hidden rounded-[6px] border",
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
      {camera ? (
        <div className="relative aspect-video bg-muted">
          {camera}
          <div className="absolute left-2 top-2 rounded-[6px] bg-background/80 px-1.5 py-0.5">
            <StateBadge tone={tone} label={label} pulse={tone === "printing"} />
          </div>
        </div>
      ) : null}
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
              className="mt-1"
              checked={!!selected}
              onCheckedChange={(v) => onSelectedChange(v === true)}
              aria-label={t("card.select", { name: printer.name })}
            />
          ) : null}
          <div className="min-w-0 flex-1">
            <CardHeader
              printer={printer}
              status={status}
              onInfo={onInfo}
              onDiagnose={onDiagnose}
              extra={renderSlot(headerExtra, ctx)}
              imageClassName={effective === "XL" ? "size-20" : "size-14"}
            />
          </div>
        </div>
        {!camera ? (
          <div>
            <StateBadge tone={tone} label={label} pulse={tone === "printing"} />
          </div>
        ) : null}
        <BadgeRow printer={printer} status={status} queueCount={upNext.length} onHms={onHms} extra={renderSlot(badgeExtra, ctx)} />
        <StatusSection
          printerId={printer.id}
          status={status}
          activeJob={activeJob}
          lastRuns={item.last_runs}
          canControl={canControl}
        />
        {status.online ? <TempFanTiles printer={printer} status={status} canControl={canControl} /> : null}
        <ControlsSection printer={printer} status={status} canControl={canControl} />
        <FilamentsSection printer={printer} status={status} canControl={canControl} mapping={amsMapping} />
        {upNext.length > 0 ? <UpNextList entries={upNext} printerId={printer.id} /> : null}
        <CardFooter printerId={printer.id} canPrint={canPrint} online={status.online} extra={renderSlot(footerExtra, ctx)} />
      </div>
    </div>
  );
}
