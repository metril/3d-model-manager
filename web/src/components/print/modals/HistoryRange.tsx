import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";

const RANGE_HOURS = [1, 6, 24, 168] as const;

export function HistoryRange({ hours, onChange }: { hours: number; onChange: (h: number) => void }) {
  const { t } = useTranslation();
  return (
    <div role="group" aria-label={t("history.range")} className="flex gap-1">
      {RANGE_HOURS.map((h) => (
        <Button key={h} type="button" size="sm" variant={hours === h ? "default" : "outline"} aria-pressed={hours === h} onClick={() => onChange(h)}>
          {t(`history.h${h}`)}
        </Button>
      ))}
    </div>
  );
}
