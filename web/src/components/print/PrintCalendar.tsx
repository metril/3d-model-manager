/** History -> Calendar tab: month grid, each day shaded by its ok/failed ratio. */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { useArchiveCalendar } from "@/api/archives";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const pad = (n: number) => String(n).padStart(2, "0");
const ymd = (y: number, m: number, d: number) => `${y}-${pad(m + 1)}-${pad(d)}`;

/** Tailwind classes by ok share: all ok = green, mixed = amber, mostly failed = red. */
function dayTone(ok: number, failed: number): string {
  const total = ok + failed;
  if (total === 0) return "bg-muted/40";
  const ratio = ok / total;
  if (ratio >= 0.8) return "bg-success/25 border-success/50";
  if (ratio >= 0.5) return "bg-warning/25 border-warning/50";
  return "bg-destructive/25 border-destructive/50";
}

export function PrintCalendar({ onSelectDay }: { onSelectDay: (date: string) => void }) {
  const { t } = useTranslation();
  const [cursor, setCursor] = useState(() => {
    const n = new Date();
    return { y: n.getFullYear(), m: n.getMonth() };
  });
  const daysInMonth = new Date(cursor.y, cursor.m + 1, 0).getDate();
  const from = ymd(cursor.y, cursor.m, 1);
  const to = ymd(cursor.y, cursor.m, daysInMonth);
  const q = useArchiveCalendar(from, to);
  const byDate = useMemo(() => new Map((q.data ?? []).map((d) => [d.date, d])), [q.data]);
  const lead = new Date(cursor.y, cursor.m, 1).getDay();
  const label = new Date(cursor.y, cursor.m, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const shift = (delta: number) =>
    setCursor((c) => {
      const d = new Date(c.y, c.m + delta, 1);
      return { y: d.getFullYear(), m: d.getMonth() };
    });
  const weekdays = Array.from({ length: 7 }, (_, i) =>
    new Date(2024, 0, 7 + i).toLocaleDateString(undefined, { weekday: "short" }),
  );

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>{label}</CardTitle>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" aria-label={t("archiveX.calendar.prev")} onClick={() => shift(-1)}>
            &lt;
          </Button>
          <Button size="sm" variant="outline" aria-label={t("archiveX.calendar.next")} onClick={() => shift(1)}>
            &gt;
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {q.isLoading ? (
          <Skeleton className="h-64 w-full rounded-lg" />
        ) : (
          <div className="grid grid-cols-7 gap-1.5 text-xs">
            {weekdays.map((w) => (
              <div key={w} className="text-center text-muted-foreground">
                {w}
              </div>
            ))}
            {Array.from({ length: lead }, (_, i) => (
              <div key={`pad${i}`} />
            ))}
            {Array.from({ length: daysInMonth }, (_, i) => {
              const date = ymd(cursor.y, cursor.m, i + 1);
              const day = byDate.get(date);
              const has = !!day && day.count > 0;
              return (
                <button
                  key={date}
                  type="button"
                  disabled={!has}
                  data-testid={`day-${date}`}
                  aria-label={has ? t("archiveX.calendar.dayLabel", { date, count: day.count }) : date}
                  onClick={() => onSelectDay(date)}
                  className={cn(
                    "flex min-h-16 flex-col items-start rounded-md border border-transparent p-1.5 text-left",
                    has ? dayTone(day.ok, day.failed) : "bg-muted/20 text-muted-foreground",
                    has && "cursor-pointer hover:ring-2 hover:ring-ring",
                  )}
                >
                  <span className="font-medium">{i + 1}</span>
                  {has ? (
                    <span className="font-mono tabular-nums">
                      {day.count} · {day.hours.toFixed(1)}h
                    </span>
                  ) : null}
                </button>
              );
            })}
          </div>
        )}
        <ul className="flex flex-wrap gap-4 text-xs text-muted-foreground">
          {(
            [
              ["bg-success/25 border-success/50", "legendOk"],
              ["bg-warning/25 border-warning/50", "legendMixed"],
              ["bg-destructive/25 border-destructive/50", "legendFailed"],
            ] as const
          ).map(([cls, key]) => (
            <li key={key} className="flex items-center gap-1.5">
              <span className={cn("size-3 rounded-sm border", cls)} />
              {t(`archiveX.calendar.${key}`)}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
