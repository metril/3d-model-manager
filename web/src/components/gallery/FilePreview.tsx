import { useState } from "react";
import { Maximize2Icon, MinusIcon, PlusIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import type { StorageTreeFile } from "@/api/types";

const IMAGE_FORMATS = new Set(["png", "jpg", "webp"]);

/** Full-size source for images, the 1024px thumbnail derivative for everything else. */
function previewSrc(file: StorageTreeFile): string {
  return IMAGE_FORMATS.has(file.format) ? `/api/files/${file.id}/download` : `/api/blobs/${file.blob_hash}/thumb?size=1024`;
}

const MIN = 0.5;
const MAX = 6;

/** Zoomable preview of a file's image/thumbnail, with a fullscreen lightbox. */
export function FilePreview({ file }: { file: StorageTreeFile }) {
  const [full, setFull] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [failed, setFailed] = useState(false);
  const src = previewSrc(file);
  const clamp = (z: number) => Math.min(MAX, Math.max(MIN, z));

  if (failed) return <p className="p-4 text-sm text-muted-foreground">No preview available.</p>;
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1">
        <Button type="button" size="icon-sm" variant="outline" aria-label="Zoom out" onClick={() => setZoom((z) => clamp(z / 1.25))}>
          <MinusIcon className="size-4" />
        </Button>
        <Button type="button" size="sm" variant="ghost" aria-label="Reset zoom" onClick={() => setZoom(1)}>
          {Math.round(zoom * 100)}%
        </Button>
        <Button type="button" size="icon-sm" variant="outline" aria-label="Zoom in" onClick={() => setZoom((z) => clamp(z * 1.25))}>
          <PlusIcon className="size-4" />
        </Button>
        <Button type="button" size="icon-sm" variant="outline" aria-label="Fullscreen" onClick={() => setFull(true)}>
          <Maximize2Icon className="size-4" />
        </Button>
      </div>
      <div className="max-h-96 overflow-auto rounded-md border bg-muted/30">
        <img
          src={src}
          alt={file.name}
          onError={() => setFailed(true)}
          style={{ width: `${zoom * 100}%`, maxWidth: zoom <= 1 ? "100%" : "none" }}
          className="mx-auto block object-contain"
        />
      </div>
      <Dialog open={full} onOpenChange={setFull}>
        <DialogContent className="h-[92vh] w-[96vw] max-w-none overflow-auto sm:max-w-none">
          <DialogTitle className="truncate">{file.name}</DialogTitle>
          <DialogDescription className="sr-only">Fullscreen preview</DialogDescription>
          <img src={src} alt={`${file.name} (fullscreen)`} className="mx-auto max-h-full max-w-full object-contain" />
        </DialogContent>
      </Dialog>
    </div>
  );
}
