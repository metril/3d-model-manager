import { useEffect, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Lightbulb,
  LightbulbOff,
  Maximize2,
  Minimize2,
  RefreshCw,
  Video,
} from "lucide-react";

import { usePrinterCamera, usePrinterCommand, usePrinterStatus, useTogglePrinterLight } from "@/api/printers";
import type { PrinterOut } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { isHttpUrl } from "@/lib/url";

export function PrinterStatusPanel({ printer }: { printer: PrinterOut }) {
  const status = usePrinterStatus(printer.id);
  const command = usePrinterCommand(printer.id);
  const toggleLight = useTogglePrinterLight(printer.id);
  const [cameraOpen, setCameraOpen] = useState(true);
  const [streamKey, setStreamKey] = useState(0);
  const [streamError, setStreamError] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [optimisticLight, setOptimisticLight] = useState<boolean | null>(null);
  const videoContainerRef = useRef<HTMLDivElement>(null);

  const camera = usePrinterCamera(printer.id, { enabled: cameraOpen });

  useEffect(() => {
    const onFullscreenChange = () => {
      setIsFullscreen(document.fullscreenElement === videoContainerRef.current);
    };
    document.addEventListener("fullscreenchange", onFullscreenChange);
    return () => document.removeEventListener("fullscreenchange", onFullscreenChange);
  }, []);

  const toggleFullscreen = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!videoContainerRef.current) return;
    if (!document.fullscreenElement) {
      videoContainerRef.current.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  };

  const s = status.data;
  const gs = s?.gcode_state ?? null;
  const printing = gs === "RUNNING";
  const paused = gs === "PAUSE";
  const hasLight = Boolean(s?.online && (s.light_on != null || printer.kind === "moonraker"));
  const isLightOn = optimisticLight !== null ? optimisticLight : Boolean(s?.light_on);

  useEffect(() => {
    if (s?.light_on !== undefined && s?.light_on !== null) {
      setOptimisticLight(null);
    }
  }, [s?.light_on]);

  return (
    <Card className="overflow-hidden">
      <CardHeader
        className="cursor-pointer select-none transition-colors hover:bg-muted/30"
        onClick={() => setCameraOpen((v) => !v)}
      >
        <CardTitle>{printer.name}</CardTitle>
        <CardDescription>
          {printer.host} · {printer.serial}
        </CardDescription>
        <CardAction>
          <div className="flex items-center gap-2">
            {hasLight ? (
              <Button
                size="sm"
                variant={isLightOn ? "secondary" : "outline"}
                className={`h-7 gap-1.5 text-xs ${isLightOn ? "border-amber-500/40 text-amber-600 dark:text-amber-400 bg-amber-500/10" : ""}`}
                onClick={(e) => {
                  e.stopPropagation();
                  const next = !isLightOn;
                  setOptimisticLight(next);
                  toggleLight.mutate(next, {
                    onSettled: () => setOptimisticLight(null),
                  });
                }}
                disabled={toggleLight.isPending}
                title={isLightOn ? "Turn chamber light off" : "Turn chamber light on"}
              >
                {isLightOn ? (
                  <Lightbulb className="size-3.5 fill-amber-400 text-amber-500" />
                ) : (
                  <LightbulbOff className="size-3.5 text-muted-foreground" />
                )}
                <span>{isLightOn ? "Light On" : "Light Off"}</span>
              </Button>
            ) : null}
            <Button
              size="sm"
              variant={cameraOpen ? "secondary" : "outline"}
              className="h-7 gap-1.5 text-xs"
              onClick={(e) => {
                e.stopPropagation();
                setCameraOpen((v) => !v);
              }}
              title={cameraOpen ? "Hide camera" : "Show camera"}
            >
              <Video className="size-3.5" />
              <span>Camera</span>
              {cameraOpen ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />}
            </Button>
            <Badge variant={s?.online ? "default" : "outline"}>
              {s?.online ? (gs ?? "online") : "offline"}
            </Badge>
          </div>
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-3">
        {cameraOpen ? (
          <div className="space-y-2 pb-1">
            {camera.isLoading ? (
              <Skeleton className="aspect-[4/3] w-full rounded-lg" />
            ) : camera.isError || !camera.data?.available || !camera.data.stream_url ? (
              <div className="flex items-center justify-center rounded-lg border border-dashed p-6 text-xs text-muted-foreground">
                No camera feed available for this printer.
              </div>
            ) : (
              <div
                ref={videoContainerRef}
                className="group relative flex aspect-[4/3] w-full items-center justify-center overflow-hidden rounded-lg bg-black"
              >
                <img
                  key={streamKey}
                  src={camera.data.stream_url}
                  referrerPolicy="no-referrer"
                  alt={camera.data.name || `${printer.name} camera`}
                  className="h-full w-full object-contain"
                  onError={() => setStreamError(true)}
                  onLoad={() => setStreamError(false)}
                />

                {/* Live Indicator */}
                <div className="absolute top-2.5 left-2.5 flex items-center gap-1.5 rounded-md bg-black/60 px-2 py-1 text-[11px] font-medium text-white shadow-xs backdrop-blur-xs">
                  <span className="relative flex h-2 w-2">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-400 opacity-75" />
                    <span className="relative inline-flex h-2 w-2 rounded-full bg-red-500" />
                  </span>
                  LIVE
                </div>

                {/* Stream Controls */}
                <div className="absolute top-2.5 right-2.5 flex items-center gap-1 opacity-90 transition-opacity group-hover:opacity-100">
                  {hasLight ? (
                    <Button
                      size="icon-xs"
                      variant="ghost"
                      className={`size-7 bg-black/60 text-white hover:bg-black/80 hover:text-white ${isLightOn ? "text-amber-400 hover:text-amber-300" : ""}`}
                      title={isLightOn ? "Turn off chamber light" : "Turn on chamber light"}
                      onClick={(e) => {
                        e.stopPropagation();
                        const next = !isLightOn;
                        setOptimisticLight(next);
                        toggleLight.mutate(next, {
                          onSettled: () => setOptimisticLight(null),
                        });
                      }}
                      disabled={toggleLight.isPending}
                    >
                      <Lightbulb className={`size-3.5 ${isLightOn ? "fill-amber-400" : ""}`} />
                    </Button>
                  ) : null}
                  <Button
                    size="icon-xs"
                    variant="ghost"
                    className="size-7 bg-black/60 text-white hover:bg-black/80 hover:text-white"
                    title="Reload stream"
                    onClick={(e) => {
                      e.stopPropagation();
                      setStreamError(false);
                      setStreamKey((k) => k + 1);
                    }}
                  >
                    <RefreshCw className="size-3.5" />
                  </Button>
                  {isHttpUrl(camera.data.direct_stream_url) ? (
                    <Button
                      size="icon-xs"
                      variant="ghost"
                      asChild
                      className="size-7 bg-black/60 text-white hover:bg-black/80 hover:text-white"
                      title="Open direct camera stream"
                    >
                      <a
                        href={camera.data.direct_stream_url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <ExternalLink className="size-3.5" />
                      </a>
                    </Button>
                  ) : null}
                  <Button
                    size="icon-xs"
                    variant="ghost"
                    className="size-7 bg-black/60 text-white hover:bg-black/80 hover:text-white"
                    title={isFullscreen ? "Exit fullscreen" : "Fullscreen"}
                    onClick={toggleFullscreen}
                  >
                    {isFullscreen ? (
                      <Minimize2 className="size-3.5" />
                    ) : (
                      <Maximize2 className="size-3.5" />
                    )}
                  </Button>
                </div>

                {streamError ? (
                  <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/80 p-4 text-center text-sm text-white">
                    <p>Camera stream disconnected or unreachable.</p>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={(e) => {
                        e.stopPropagation();
                        setStreamError(false);
                        setStreamKey((k) => k + 1);
                      }}
                    >
                      Retry
                    </Button>
                  </div>
                ) : null}
              </div>
            )}
          </div>
        ) : null}

        {!s?.online ? (
          <p className="text-sm text-muted-foreground">Printer offline or printerd not running.</p>
        ) : (
          <>
            <div className="h-2 w-full overflow-hidden rounded bg-muted">
              <div className="h-2 rounded bg-primary transition-all" style={{ width: `${s.mc_percent ?? 0}%` }} />
            </div>
            <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
              <span>{s.mc_percent ?? 0}%</span>
              <span>
                layer {s.layer_num ?? 0}/{s.total_layer_num ?? 0}
              </span>
              <span>{s.mc_remaining_time != null ? `${s.mc_remaining_time} min left` : "--"}</span>
              <span>nozzle {s.nozzle_temper ?? "--"}°</span>
              <span>bed {s.bed_temper ?? "--"}°</span>
            </div>
            {s.subtask_name ? <p className="text-sm">{s.subtask_name}</p> : null}
            {s.print_error ? (
              <p role="alert" className="text-sm text-destructive">
                Printer error {s.print_error}
              </p>
            ) : null}
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={!printing || command.isPending}
                onClick={() => command.mutate("pause")}
              >
                Pause
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={!paused || command.isPending}
                onClick={() => command.mutate("resume")}
              >
                Resume
              </Button>
              <Button
                size="sm"
                variant="destructive"
                disabled={!(printing || paused) || command.isPending}
                onClick={() => command.mutate("stop")}
              >
                Stop
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
