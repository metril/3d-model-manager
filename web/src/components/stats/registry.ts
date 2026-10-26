import type { StatsDashboard } from "@/api/stats";
import { StockForecastWidget } from "@/components/inventory/ForecastPanel";
import { Failures, Filament, Heatmap, Printers, QuickStats, Records, SuccessRate, TimeAccuracy } from "@/components/stats/widgets";
import type { WidgetId } from "@/lib/statsLayout";

export const WIDGETS: Record<WidgetId, (props: { d: StatsDashboard }) => import("react").ReactElement> = {
  quick: QuickStats,
  success: SuccessRate,
  accuracy: TimeAccuracy,
  failures: Failures,
  heatmap: Heatmap,
  records: Records,
  printers: Printers,
  filament: Filament,
  stock: StockForecastWidget,
};
