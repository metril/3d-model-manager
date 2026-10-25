/**
 * "Slice" action for an unsliced source file (Files tab rows, model header):
 * opens the recipe picker and starts a slice-only job. Self-hides unless the
 * slicer is configured, the viewer may print, and the file is a format the
 * sidecar accepts.
 */
import { useState } from "react";
import { ScissorsIcon } from "lucide-react";

import { useAuth } from "@/api/auth";
import { useFeatures } from "@/api/features";
import type { FileOut } from "@/api/types";
import { SliceDialog } from "@/components/slice/SliceDialog";
import { isSliceable } from "@/components/slice/sliceable";
import { Button } from "@/components/ui/button";

export function SliceFileButton({
  file,
  size = "icon-sm",
}: {
  file: Pick<FileOut, "id" | "rel_path" | "format" | "verified_at">;
  size?: "icon-sm" | "default";
}) {
  const features = useFeatures();
  const { can } = useAuth();
  const [open, setOpen] = useState(false);
  if (!features.data?.slicer_configured || !can("printers:print") || !isSliceable(file)) return null;
  return (
    <>
      {size === "default" ? (
        <Button type="button" variant="outline" onClick={() => setOpen(true)}>
          <ScissorsIcon className="size-4" />
          Slice…
        </Button>
      ) : (
        <Button type="button" variant="ghost" size="icon-sm" aria-label={`Slice ${file.rel_path}`} onClick={() => setOpen(true)}>
          <ScissorsIcon className="size-4" />
        </Button>
      )}
      <SliceDialog open={open} onOpenChange={setOpen} file={file} />
    </>
  );
}
