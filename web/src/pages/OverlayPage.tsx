import { useQuery } from "@tanstack/react-query";
import { useParams, useSearch } from "@tanstack/react-router";
import { useEffect } from "react";

import type { OverlayOut } from "@/api/types";
import { CameraTile } from "@/components/print/CameraTile";
import { formatEta } from "@/lib/formatEta";

const POLL_MS = 5_000;

/** Public overlay fetch: deliberately NOT `api.get` (its 401 handler hard
 * redirects to /login, wrong for an OBS browser source). */
async function fetchOverlay(id: number, token: string): Promise<OverlayOut> {
  const res = await fetch(`/api/overlay/${id}?token=${encodeURIComponent(token)}`);
  if (!res.ok) throw new Error(`overlay ${res.status}`);
  return (await res.json()) as OverlayOut;
}

/** OBS browser-source page: transparent background, camera feed (token
 * URLs from the public route) plus a status strip; polls every 5 s. */
export function OverlayView({ id, token }: { id: number; token: string | undefined }) {
  const query = useQuery({
    queryKey: ["overlay", id, token] as const,
    queryFn: () => fetchOverlay(id, token!),
    enabled: !!token,
    refetchInterval: POLL_MS,
    retry: false,
  });

  useEffect(() => {
    const prev = [document.documentElement.style.background, document.body.style.background];
    document.documentElement.style.background = "transparent";
    document.body.style.background = "transparent";
    return () => {
      document.documentElement.style.background = prev[0]!;
      document.body.style.background = prev[1]!;
    };
  }, []);

  const d = query.data;
  if (!token || (query.isError && !d)) {
    return <div className="p-2 text-xs text-white/70">{!token ? "Missing overlay token" : "Overlay unavailable"}</div>;
  }
  if (!d) return null;
  const eta = formatEta(d.remaining_min);

  return (
    <div className="relative h-svh w-svw overflow-hidden bg-transparent text-white">
      <CameraTile
        printerId={id}
        online
        urls={{ stream_url: d.stream_url, snapshot_url: d.snapshot_url }}
        rotation={d.rotation}
        controls={false}
        className="bg-transparent"
      />
      <div
        data-testid="overlay-strip"
        className="absolute inset-x-0 bottom-0 flex flex-wrap items-center gap-x-4 gap-y-1 bg-black/60 px-4 py-2 text-sm font-medium"
      >
        <span>{d.printer_name}</span>
        <span>{d.gcode_state ?? "-"}</span>
        {d.progress != null ? <span>{d.progress}%</span> : null}
        {d.layer != null && d.total_layers ? (
          <span>
            Layer {d.layer}/{d.total_layers}
          </span>
        ) : null}
        {eta ? <span>ETA {eta}</span> : null}
      </div>
    </div>
  );
}

export function OverlayPage() {
  const { id } = useParams({ strict: false }) as { id: string };
  const { token } = useSearch({ strict: false }) as { token?: string };
  return <OverlayView id={Number(id)} token={token} />;
}
