/**
 * Per-sliced-file "Print" button (Files tab): only renders for a sliced
 * `.gcode.3mf` blob (or, with the slicer sidecar configured, a sliceable
 * 3mf/stl/step/obj source, opened in recipe mode), and only while the printer
 * feature is on and at least one printer is configured -- otherwise it
 * self-hides rather than showing a disabled control.
 *
 * P5 Task 11: the dialog it opens is now the shared `PrintDialog` (5-step
 * plan/filament/send flow) instead of the old plate+AMS `SendToPrinterDialog`
 * -- every launcher (Files tab, queue, up-next, model header, run detail's
 * Reprint, command palette) opens the same component now.
 */
import { useState } from "react";
import { PrinterIcon } from "lucide-react";

import { useAuth } from "@/api/auth";
import { useFeatures } from "@/api/features";
import { usePrinters } from "@/api/printers";
import type { FileOut } from "@/api/types";
import { Button } from "@/components/ui/button";
import { PrintDialog } from "@/components/print/PrintDialog";

export function SendToPrinterButton({ file }: { file: FileOut }) {
  const features = useFeatures();
  const enabled = !!features.data?.printer_enabled;
  const printers = usePrinters({ enabled });
  const { canPrinter } = useAuth();
  const [open, setOpen] = useState(false);
  const allowed = (printers.data ?? []).filter((p) => canPrinter(p.id, "print"));

  const sliced = file.format === "gcode_3mf";
  const sliceable = !!features.data?.slicer_configured && ["3mf", "stl", "step", "obj"].includes(file.format);
  if (!enabled || !(sliced || sliceable) || allowed.length === 0) return null;
  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label={sliced ? `Print ${file.rel_path}` : `Slice and print ${file.rel_path}`}
        onClick={() => setOpen(true)}
      >
        <PrinterIcon className="size-4" />
      </Button>
      <PrintDialog open={open} onOpenChange={setOpen} source={{ kind: "file", fileId: file.id }} sliceOnly={!sliced} />
    </>
  );
}
