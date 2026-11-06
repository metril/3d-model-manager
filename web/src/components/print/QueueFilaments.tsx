/**
 * Queue-row filament chips (required slots + mapped spool), the colour
 * mismatch warning chip, and the thumbnail with a larger hover preview.
 */
import { AlertTriangleIcon } from "lucide-react";
import { HoverCard } from "radix-ui";
import type { ReactNode } from "react";

import type { QueueEntry } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { FilamentChip } from "@/components/ui/filament-chip";
import { formatCost } from "@/lib/format";
import { useTranslation } from "react-i18next";

export function QueueFilamentChips({ entry }: { entry: QueueEntry }) {
  const { t } = useTranslation();
  const filaments = entry.filaments ?? [];
  if (filaments.length === 0) return null;
  const mismatched = filaments.filter((f) => f.colour_mismatch);
  return (
    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1" data-testid="queue-filaments">
      {filaments.map((f) => (
        <span key={f.slot} className="inline-flex items-center gap-1" data-testid={`queue-filament-${f.slot}`}>
          <FilamentChip color={f.color ?? undefined} material={f.type ?? undefined} label={`#${f.slot}`} />
          {f.spool_id != null ? (
            <>
              <span aria-hidden className="text-xs text-muted-foreground">
                →
              </span>
              <FilamentChip color={f.spool_color ?? undefined} label={f.spool_name ?? t("queueFilaments.spool", { id: f.spool_id })} />
            </>
          ) : null}
        </span>
      ))}
      {mismatched.length > 0 ? (
        <Badge
          variant="outline"
          className="gap-1 bg-warning/15 text-warning"
          title={mismatched
            .map(
              (f) =>
                t("queueFilaments.mismatchLine", {
                  slot: f.slot,
                  want: f.color ?? "?",
                  have: f.spool_color ?? "?",
                }),
            )
            .join("\n")}
        >
          <AlertTriangleIcon className="size-3" />
          {t("queueFilaments.mismatch", { slots: mismatched.map((f) => f.slot).join(", ") })}
        </Badge>
      ) : null}
      {entry.estimated_cost != null ? (
        <span className="text-xs text-muted-foreground" title={t("queueFilaments.costTitle")}>
          {t("queueFilaments.est", { cost: formatCost(entry.estimated_cost) })}
        </span>
      ) : null}
    </div>
  );
}

/** Thumbnail that opens a larger preview on hover/focus. */
export function HoverPreview({ src, children }: { src: string | null | undefined; children: ReactNode }) {
  if (!src) return <>{children}</>;
  return (
    <HoverCard.Root openDelay={200} closeDelay={80}>
      <HoverCard.Trigger asChild>
        <div data-testid="queue-thumb">{children}</div>
      </HoverCard.Trigger>
      <HoverCard.Portal>
        <HoverCard.Content
          side="right"
          sideOffset={8}
          className="z-50 rounded-lg bg-popover p-1.5 shadow-elevation-2 ring-1 ring-foreground/10"
        >
          <img src={src} alt="" className="size-64 rounded object-contain" />
        </HoverCard.Content>
      </HoverCard.Portal>
    </HoverCard.Root>
  );
}
