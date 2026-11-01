import { useState } from "react";

import { formatIcon } from "@/lib/formatMeta";
import { cn } from "@/lib/utils";
import type { FileOut } from "@/api/types";

export function FileThumb({ file, className }: { file: FileOut; className?: string }) {
  const [errored, setErrored] = useState(false);
  const Icon = formatIcon(file.format);

  if (file.thumb_ready && !errored) {
    return (
      <img
        src={`/api/blobs/${file.blob_hash}/thumb?size=256`}
        alt={file.rel_path}
        loading="lazy"
        className={cn("size-10 rounded object-cover", className)}
        onError={() => setErrored(true)}
      />
    );
  }

  return (
    <div className={cn("flex size-10 items-center justify-center rounded bg-muted text-muted-foreground", className)}>
      <Icon className="size-5" />
    </div>
  );
}
