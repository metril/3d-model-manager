import { CameraTile } from "@/components/print/CameraTile";
import { HistoryStats } from "@/components/print/HistoryStats";
import { RecentRunsList } from "@/components/print/RecentRunsList";
import { UpNextList } from "@/components/print/UpNextList";
import { StockForecastWidget } from "@/components/inventory/ForecastPanel";
import { usePrintDash } from "./PrintDashContext";

/** The right rail as one stack: queue, extras (review inbox), recent archives. */
export function RailStack() {
  const { upNext, recentRuns, railExtras } = usePrintDash();
  return (
    <div className="flex flex-col gap-6">
      <UpNextList entries={upNext} />
      <RecentRunsList runs={recentRuns} />
      {railExtras}
    </div>
  );
}

export function QueuePanel() {
  const { upNext, railExtras } = usePrintDash();
  return (
    <div className="flex flex-col gap-4">
      <UpNextList entries={upNext} />
      {railExtras}
    </div>
  );
}

export function ArchivesPanel() {
  return <RecentRunsList runs={usePrintDash().recentRuns} />;
}

/** A live tile per printer the user may print on. */
export function CameraPanel() {
  const { printers } = usePrintDash();
  const cams = printers.filter((p) => p.can_print);
  if (cams.length === 0) return <p className="text-sm text-muted-foreground">No cameras available.</p>;
  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(min(320px, 100%), 1fr))" }}>
      {cams.map((p) => (
        <div key={p.printer.id} className="flex flex-col gap-1">
          <span className="text-xs font-medium">{p.printer.name}</span>
          <CameraTile printerId={p.printer.id} online={p.status.online} />
        </div>
      ))}
    </div>
  );
}

export function StatsPanel() {
  return <HistoryStats />;
}

export function ForecastPanel() {
  return <StockForecastWidget />;
}
