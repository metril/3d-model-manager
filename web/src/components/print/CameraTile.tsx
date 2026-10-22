/**
 * Lazy MJPEG camera tile (P4 Task 8): mints a scoped camera token
 * (`useCameraUrls`) once the printer reports `online`, then streams the
 * printer's live feed as a plain `<img>` -- the browser keeps pulling
 * MJPEG frames for as long as that tag stays mounted, so "pause" is
 * enforced by unmounting it rather than any play/pause API. It unmounts
 * off-screen (`IntersectionObserver`, 0.1 threshold -- missing IO support
 * is treated as visible, not permanently paused) or while the tab is
 * hidden (`document.visibilityState`), which lets printerd's upstream
 * camera worker see the viewer disappear and idle-stop (P4 Task 5). The
 * signed snapshot URL renders as a CSS background "poster" so the tile
 * isn't blank while the stream's first frame is in flight. `online: false`
 * or an img `onError` both show the offline state (muted icon + copy) and
 * retry by refetching a fresh token after 10s; the token mint itself is
 * skipped entirely while offline.
 */
import { VideoOffIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useCameraUrls } from "@/api/printers";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const RETRY_MS = 10_000;

/** Visible enough to keep the MJPEG `<img>` mounted: on-screen past the
 * 0.1 IntersectionObserver threshold, and the tab itself isn't hidden. */
function useVisible(target: React.RefObject<HTMLElement | null>): boolean {
  const [intersecting, setIntersecting] = useState(true);
  const [hidden, setHidden] = useState(() => document.visibilityState === "hidden");

  useEffect(() => {
    if (typeof IntersectionObserver === "undefined" || !target.current) return;
    const observer = new IntersectionObserver(([entry]) => setIntersecting(entry?.isIntersecting ?? true), {
      threshold: 0.1,
    });
    observer.observe(target.current);
    return () => observer.disconnect();
  }, [target]);

  useEffect(() => {
    const onVisibility = () => setHidden(document.visibilityState === "hidden");
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  return intersecting && !hidden;
}

export function CameraTile({
  printerId,
  online,
  className,
}: {
  printerId: number;
  online: boolean;
  className?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const visible = useVisible(containerRef);
  const [loaded, setLoaded] = useState(false);
  const [errored, setErrored] = useState(false);
  const tokens = useCameraUrls(printerId, { enabled: online });

  // Auto-retry (10s) after a stream error: refetch a fresh token and give
  // the `<img>` another try. Offline because `online` is false isn't
  // retried here -- that state is driven by the printer's own status poll.
  useEffect(() => {
    if (!errored) return;
    const timer = setTimeout(() => {
      setErrored(false);
      setLoaded(false);
      void tokens.refetch();
    }, RETRY_MS);
    return () => clearTimeout(timer);
  }, [errored, tokens]);

  // A printer going back online clears any stale error from a previous session.
  useEffect(() => {
    if (!online) setErrored(false);
  }, [online]);

  const offline = !online || errored;
  const showImg = !offline && visible && tokens.data != null;

  return (
    <div
      ref={containerRef}
      data-slot="camera-tile"
      className={cn("relative size-full overflow-hidden bg-muted", className)}
      style={
        !offline && tokens.data
          ? {
              backgroundImage: `url(${tokens.data.snapshot_url})`,
              backgroundSize: "cover",
              backgroundPosition: "center",
            }
          : undefined
      }
    >
      {offline ? (
        <div className="flex size-full flex-col items-center justify-center gap-1.5 text-muted-foreground">
          <VideoOffIcon className="size-6" aria-hidden="true" />
          <span className="label-caps text-xs">Camera offline</span>
        </div>
      ) : (
        <>
          {showImg ? (
            <img
              src={tokens.data!.stream_url}
              alt="Camera feed"
              className={cn("size-full object-cover transition-opacity", loaded ? "opacity-100" : "opacity-0")}
              onLoad={() => setLoaded(true)}
              onError={() => setErrored(true)}
            />
          ) : null}
          {!loaded ? <Skeleton className="absolute inset-0 size-full rounded-none" /> : null}
        </>
      )}
    </div>
  );
}
