/**
 * Per-sliced-file "Print" button (Files tab): only renders for a sliced
 * `.gcode.3mf` blob (`format === "gcode_3mf"`), and only while the printer
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

  if (!enabled || file.format !== "gcode_3mf" || allowed.length === 0) return null;
  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label={`Print ${file.rel_path}`}
        onClick={() => setOpen(true)}
      >
        <PrinterIcon className="size-4" />
      </Button>
      <PrintDialog open={open} onOpenChange={setOpen} source={{ kind: "file", fileId: file.id }} />
    </>
  );
}
