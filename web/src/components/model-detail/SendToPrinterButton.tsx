/**
 * Per-sliced-file "Print" button + send dialog (M4 Task 8, Files tab): only
 * renders for a sliced `.gcode.3mf` blob (`format === "gcode_3mf"`), and
 * only while the printer feature is on and at least one printer is
 * configured -- otherwise it self-hides rather than showing a disabled
 * control. Submits `POST /printers/{id}/print`.
 *
 * The dialog itself is `SendToPrinterDialog` (Round 8 Task 3), extracted
 * out to a controlled (`open`/`onOpenChange`) component so the print
 * queue's row-level "Print" action (`QueuePage.tsx`) can drive the SAME
 * form/submit logic behind its own trigger, rather than duplicating it.
 * `SendToPrinterButton` below keeps its exact prior behavior/appearance --
 * it just now owns the `open` state itself and renders its trigger button
 * as a plain sibling instead of a `DialogTrigger`.
 */
import { useState } from "react";
import { PrinterIcon } from "lucide-react";

import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useFeatures } from "@/api/features";
import { usePrinters, useStartPrint } from "@/api/printers";
import type { FileOut, PrinterOut } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

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
      <SendToPrinterDialog file={file} printers={allowed} open={open} onOpenChange={setOpen} />
    </>
  );
}

/** `{ file, printers, open, onOpenChange, onSuccess? }` -- the plate/AMS
 * form + submit mutation are unchanged from before the extraction.
 * `onSuccess` fires (in addition to closing the dialog) after a successful
 * start-print mutation, letting a caller react to "this file is now on its
 * way to a printer" (the print queue removes the row + toasts). */
export function SendToPrinterDialog({
  file,
  printers,
  open,
  onOpenChange,
  onSuccess,
}: {
  file: FileOut;
  printers: PrinterOut[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess?: () => void;
}) {
  const [printerId, setPrinterId] = useState(printers[0].id);
  const [plate, setPlate] = useState(file.meta?.plates?.[0]?.index ?? 1);
  const [useAms, setUseAms] = useState(false);
  const [bedLevelling, setBedLevelling] = useState(true);
  const [flowCali, setFlowCali] = useState(true);
  const [timelapse, setTimelapse] = useState(false);
  const start = useStartPrint(printerId);
  const plates = file.meta?.plates ?? [];

  function submit() {
    start.mutate(
      {
        file_id: file.id,
        plate,
        use_ams: useAms,
        ams_mapping: [0],
        bed_levelling: bedLevelling,
        flow_cali: flowCali,
        timelapse,
      },
      {
        onSuccess: () => {
          onOpenChange(false);
          onSuccess?.();
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Send {file.rel_path} to a printer</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <label className="block text-sm">
            Printer
            <Select value={String(printerId)} onValueChange={(v) => setPrinterId(Number(v))}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {printers.map((p) => (
                  <SelectItem key={p.id} value={String(p.id)}>
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          {plates.length > 1 ? (
            <label className="block text-sm">
              Plate
              <Select value={String(plate)} onValueChange={(v) => setPlate(Number(v))}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {plates.map((pl) => (
                    <SelectItem key={pl.index} value={String(pl.index)}>
                      Plate {pl.index}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
          ) : null}
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={useAms} onChange={(e) => setUseAms(e.target.checked)} /> Use AMS
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={bedLevelling} onChange={(e) => setBedLevelling(e.target.checked)} /> Bed
            levelling
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={flowCali} onChange={(e) => setFlowCali(e.target.checked)} /> Flow
            calibration
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={timelapse} onChange={(e) => setTimelapse(e.target.checked)} /> Timelapse
          </label>
          {start.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {start.error instanceof ApiError ? start.error.detail : "Could not start print"}
            </p>
          ) : null}
        </div>
        <DialogFooter>
          <Button type="button" onClick={submit} disabled={start.isPending}>
            {start.isPending ? "Sending..." : "Send to printer"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
