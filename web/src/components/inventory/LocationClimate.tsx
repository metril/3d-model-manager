import { useTranslation } from "react-i18next";

import { useDryingSettings } from "@/api/drying";
import type { LocationSensor } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { formatDateTime } from "@/lib/format";
import { locationClimate } from "@/lib/locationClimate";

/** Humidity / temperature chip for a storage location, with stale + over-threshold flags. */
export function LocationClimate({ sensors }: { sensors: LocationSensor[] | undefined }) {
  const { t } = useTranslation();
  const settings = useDryingSettings();
  const fair = typeof settings.data?.ams_humidity_fair === "number" ? settings.data.ams_humidity_fair : undefined;
  const c = locationClimate(sensors, fair);
  if (!c) return null;
  const title = c.updatedAt ? t("spoolAdmin.climate.updated", { when: formatDateTime(c.updatedAt) }) : t("spoolAdmin.climate.never");
  return (
    <span
      data-testid="location-climate"
      title={title}
      className={`inline-flex items-center gap-1 font-mono text-xs tabular-nums ${c.warn ? "text-destructive" : "text-muted-foreground"}`}
    >
      {c.humidity != null ? <span>{Math.round(c.humidity)}%</span> : null}
      {c.temp != null ? <span>{Math.round(c.temp * 10) / 10}°</span> : null}
      {c.warn ? <Badge variant="destructive">{t("spoolAdmin.climate.humid")}</Badge> : null}
      {c.stale ? <Badge variant="outline">{t("spoolAdmin.climate.stale")}</Badge> : null}
    </span>
  );
}
