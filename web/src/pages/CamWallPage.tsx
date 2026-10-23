import { useState } from "react";

import { usePrintersOverview } from "@/api/printers";
import { CameraTile } from "@/components/print/CameraTile";
import { selectLive } from "@/lib/camWall";

const MAX_LIVE_KEY = "camWallMaxLive";
const SNAP_SEC_KEY = "camWallSnapshotSec";

function readNumber(key: string, fallback: number, min: number, max: number): number {
  try {
    const n = Number(localStorage.getItem(key));
    if (Number.isFinite(n) && n >= min && n <= max && localStorage.getItem(key) !== null) return Math.round(n);
  } catch {
    /* storage unavailable */
  }
  return fallback;
}

function persist(key: string, value: number) {
  try {
    localStorage.setItem(key, String(value));
  } catch {
    /* storage unavailable */
  }
}

/** Kiosk wall of every visible printer: at most `maxLive` tiles stream,
 * the rest refresh a snapshot every `snapshotSec`; click a still to make it
 * live (the oldest live promotion drops back). Session auth only. */
export function CamWallPage() {
  const overview = usePrintersOverview();
  const [maxLive, setMaxLive] = useState(() => readNumber(MAX_LIVE_KEY, 2, 0, 16));
  const [snapshotSec, setSnapshotSec] = useState(() => readNumber(SNAP_SEC_KEY, 10, 1, 300));
  const [promoted, setPromoted] = useState<number[]>([]);

  const items = (overview.data?.printers ?? []).filter((p) => p.can_print && p.printer.enabled);
  const ids = items.map((p) => p.printer.id);
  const live = selectLive(ids, maxLive, promoted);

  return (
    <div className="flex min-h-svh flex-col bg-black text-white">
      <div className="flex items-center gap-4 px-3 py-1.5 text-xs text-white/80">
        <span className="font-medium">Camera wall</span>
        <label className="flex items-center gap-1">
          Max live
          <input
            type="number"
            min={0}
            max={16}
            aria-label="Max live"
            value={maxLive}
            className="w-14 rounded bg-white/10 px-1"
            onChange={(e) => {
              const n = Math.min(16, Math.max(0, Number(e.target.value) || 0));
              setMaxLive(n);
              persist(MAX_LIVE_KEY, n);
            }}
          />
        </label>
        <label className="flex items-center gap-1">
          Snapshot every (s)
          <input
            type="number"
            min={1}
            max={300}
            aria-label="Snapshot seconds"
            value={snapshotSec}
            className="w-14 rounded bg-white/10 px-1"
            onChange={(e) => {
              const n = Math.min(300, Math.max(1, Number(e.target.value) || 1));
              setSnapshotSec(n);
              persist(SNAP_SEC_KEY, n);
            }}
          />
        </label>
      </div>
      <div className="grid flex-1 grid-cols-[repeat(auto-fit,minmax(320px,1fr))] gap-1 p-1">
        {items.map(({ printer, status }) => {
          const isLive = live.has(printer.id);
          return (
            <div
              key={`${printer.id}-${isLive ? "live" : "snap"}`}
              data-testid={`wall-tile-${printer.id}`}
              data-live={isLive}
              className="relative aspect-video"
              onClick={() => !isLive && setPromoted((p) => [printer.id, ...p.filter((x) => x !== printer.id)])}
            >
              <CameraTile
                printerId={printer.id}
                online={status.online}
                snapshotOnly={!isLive}
                snapshotSec={snapshotSec}
                controls={isLive}
                className="bg-black"
              />
              <span className="pointer-events-none absolute left-1.5 top-1.5 rounded bg-black/60 px-1.5 py-0.5 text-xs">
                {printer.name}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
