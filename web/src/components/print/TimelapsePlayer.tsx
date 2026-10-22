/**
 * An archived run's captured timelapse (P4 camera plan, Task 9): a plain
 * `<video>` against `archiveTimelapseUrl` (Range-seekable; the backend
 * serves `video/mp4` normally, or falls back to `video/x-msvideo` when
 * ffmpeg was missing at capture time -- the browser may not be able to
 * play that container at all, hence the error fallback below rather than
 * assuming every served file is playable inline).
 */
import { useState } from "react";

import { archiveTimelapseUrl } from "@/api/archives";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function TimelapsePlayer({
  archiveId,
  poster,
  className,
}: {
  archiveId: number;
  poster?: string;
  className?: string;
}) {
  const [unplayable, setUnplayable] = useState(false);
  const src = archiveTimelapseUrl(archiveId);

  if (unplayable) {
    return (
      <div
        className={cn(
          "flex flex-col items-center justify-center gap-2 rounded-[6px] border bg-muted p-6 text-center text-sm text-muted-foreground",
          className,
        )}
      >
        <p>Can&apos;t play in this browser</p>
        <Button asChild variant="outline" size="sm">
          <a href={src} download>
            Download
          </a>
        </Button>
      </div>
    );
  }

  return (
    <video
      controls
      playsInline
      preload="metadata"
      src={src}
      poster={poster}
      className={cn("size-full rounded-[6px] bg-black", className)}
      onError={() => setUnplayable(true)}
    />
  );
}
