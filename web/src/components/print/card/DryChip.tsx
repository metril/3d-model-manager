/** AMS unit drying chip: status + sub-status, client-side countdown, target, Stop. */
import { SquareIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useDrying } from "@/api/drying";
import type { AmsUnit } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

import { unitLabel } from "./ams";

export function formatLeft(totalSeconds: number): string {
  const mins = Math.max(0, Math.ceil(totalSeconds / 60));
  const h = Math.floor(mins / 60);
  return h > 0 ? `${h}h ${String(mins % 60).padStart(2, "0")}m` : `${mins}m`;
}

/** Remounted (via `key`) whenever the reported minutes change, which resets the client baseline. */
function Countdown({ minutes }: { minutes: number }) {
  const { t } = useTranslation();
  const [base] = useState(() => Date.now());
  const [now, setNow] = useState(base);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 15_000);
    return () => clearInterval(id);
  }, []);
  const left = minutes * 60 - (now - base) / 1000;
  return <span data-testid="dry-left">{t("drying.left", { time: formatLeft(left) })}</span>;
}

export function DryChip({
  printerId,
  id,
  unit,
  canStop,
  screenOnly,
}: {
  printerId: number;
  id: number;
  unit: AmsUnit;
  canStop: boolean;
  screenOnly: boolean;
}) {
  const { t } = useTranslation();
  const drying = useDrying(printerId);
  const st = unit.dry_status ?? 0;
  if (st <= 0) return null;
  const sub = unit.dry_sub_status ? t(`drying.sub.${unit.dry_sub_status}`, { defaultValue: "" }) : "";
  const label = t(`drying.status.${st}`, { defaultValue: "" });
  const chip = (
    <Badge
      variant={st === 5 ? "destructive" : "secondary"}
      className="gap-1 font-mono tabular-nums"
      data-testid="dry-chip"
    >
      <span>{label}</span>
      {sub ? <span className="text-muted-foreground">{sub}</span> : null}
      {unit.dry_time_min != null && st !== 5 ? <Countdown key={unit.dry_time_min} minutes={unit.dry_time_min} /> : null}
      {unit.dry_target_temp != null ? <span>{unit.dry_target_temp}°</span> : null}
      {unit.dry_filament ? <span>{unit.dry_filament}</span> : null}
    </Badge>
  );
  return (
    <span className="ml-auto flex items-center gap-1">
      {screenOnly ? (
        <TooltipProvider delayDuration={150}>
          <Tooltip>
            <TooltipTrigger asChild>{chip}</TooltipTrigger>
            <TooltipContent>{t("drying.screenOnly")}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      ) : (
        chip
      )}
      {canStop && !screenOnly ? (
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          aria-label={t("drying.stopUnit", { unit: unit.label || unitLabel(id) })}
          disabled={drying.pending}
          onClick={() => void drying.stop(id)}
        >
          <SquareIcon />
        </Button>
      ) : null}
    </span>
  );
}
