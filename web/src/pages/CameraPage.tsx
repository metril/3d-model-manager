import { useParams, useSearch } from "@tanstack/react-router";

import { usePrintersOverview } from "@/api/printers";
import { CameraTile } from "@/components/print/CameraTile";
import { formatEta } from "@/lib/formatEta";

/** Standalone camera window body (`window.open('/camera/$id')`): the tile
 * fills the viewport above a one-line status bar. */
export function CameraView({ id }: { id: number }) {
  const overview = usePrintersOverview();
  const item = overview.data?.printers.find((p) => p.printer.id === id);
  const st = item?.status;
  const eta = formatEta(st?.mc_remaining_time);

  return (
    <div className="flex h-svh w-svw flex-col bg-black text-white">
      <div className="min-h-0 flex-1">
        <CameraTile printerId={id} online={st?.online ?? false} className="bg-black" />
      </div>
      <div className="flex items-center gap-3 px-3 py-1.5 text-xs" data-testid="camera-status-bar">
        <span className="font-medium">{item?.printer.name ?? `Printer ${id}`}</span>
        <span className="text-white/70">{st?.online === false ? "OFFLINE" : (st?.gcode_state ?? "-")}</span>
        {st?.mc_percent != null ? <span>{st.mc_percent}%</span> : null}
        {st?.layer_num != null && st.total_layer_num ? (
          <span>
            Layer {st.layer_num}/{st.total_layer_num}
          </span>
        ) : null}
        {eta ? <span>ETA {eta}</span> : null}
      </div>
    </div>
  );
}

export function CameraPage() {
  const { id } = useParams({ strict: false }) as { id: string };
  return <CameraView id={Number(id)} />;
}

/** `/camera?printer=ID` (PWA home-screen shortcut): without a printer id,
 * falls back to the first printer once the overview loads. */
export function CameraShortcutPage() {
  const { printer } = useSearch({ strict: false }) as { printer?: number };
  const overview = usePrintersOverview();
  const id = printer ?? overview.data?.printers[0]?.printer.id;
  if (id == null) {
    return (
      <div className="grid h-svh w-svw place-items-center bg-black text-sm text-white/70">
        {overview.isPending ? "Loading..." : "No printers"}
      </div>
    );
  }
  return <CameraView id={id} />;
}
