/**
 * Per-printer "check plate before print" (V4): enable switch, threshold and
 * plate type (stored in `printer.options.plate_check`), a calibration wizard
 * (capture empty-plate reference frames) and a Test button that runs
 * `POST /printers/{id}/check-plate` and shows the annotated preview. Also a
 * camera diagnose action.
 */
import { useState } from "react";

import { ApiError } from "@/api/client";
import {
  referenceImageUrl,
  useCaptureReference,
  useCheckPlate,
  useDeleteReference,
  useDiagnoseCamera,
  usePlateReferences,
} from "@/api/plateCheck";
import { useUpdatePrinter } from "@/api/printers";
import type { PrinterOut } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const MAX_REFERENCES = 5;

type PlateCheckOptions = { enabled?: boolean; threshold?: number; plate_type?: string };

function errText(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.detail : fallback;
}

export function PrinterPlateCheckSection({ printer }: { printer: PrinterOut }) {
  const saved = (printer.options.plate_check ?? {}) as PlateCheckOptions;
  const [enabled, setEnabled] = useState(Boolean(saved.enabled));
  const [threshold, setThreshold] = useState(String(saved.threshold ?? 1.0));
  const [plateType, setPlateType] = useState(saved.plate_type ?? "");
  const [wizard, setWizard] = useState(false);
  const update = useUpdatePrinter(printer.id);
  const check = useCheckPlate(printer.id);
  const diagnose = useDiagnoseCamera(printer.id);
  const id = `printer-${printer.id}-platecheck`;
  const thr = Number(threshold);
  const thrValid = Number.isFinite(thr) && thr >= 0.05 && thr <= 50;

  function save() {
    update.mutate({
      options: { ...printer.options, plate_check: { enabled, threshold: thr, plate_type: plateType.trim() } },
    });
  }

  return (
    <fieldset className="flex flex-col gap-3 rounded-md border p-3">
      <legend className="px-1 text-sm font-medium">Plate check</legend>
      <p className="text-xs text-muted-foreground">
        Compare the camera view to captured empty-plate references before a queued print starts. A
        non-empty plate holds the entry (and notifies) until it is cleared.
      </p>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="flex items-center gap-2 text-sm" htmlFor={`${id}-enabled`}>
          <input
            id={`${id}-enabled`}
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          Check plate before print
        </label>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-threshold`}>Threshold (% difference)</Label>
          <Input
            id={`${id}-threshold`}
            inputMode="decimal"
            value={threshold}
            aria-invalid={!thrValid}
            onChange={(e) => setThreshold(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-plate`}>Plate type</Label>
          <Input
            id={`${id}-plate`}
            value={plateType}
            maxLength={64}
            placeholder="e.g. Textured PEI"
            onChange={(e) => setPlateType(e.target.value)}
          />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" size="sm" onClick={save} disabled={update.isPending || !thrValid}>
          {update.isPending ? "Saving..." : "Save plate check"}
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={() => setWizard(true)}>
          Calibrate
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={check.isPending || !thrValid}
          onClick={() => check.mutate({ plate_type: plateType.trim(), threshold: thr })}
        >
          {check.isPending ? "Checking..." : "Test"}
        </Button>
        <Button type="button" size="sm" variant="ghost" disabled={diagnose.isPending} onClick={() => diagnose.mutate()}>
          {diagnose.isPending ? "Diagnosing..." : "Diagnose camera"}
        </Button>
        {update.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {errText(update.error, "Could not save plate check")}
          </p>
        ) : null}
        {update.isSuccess ? <p className="text-sm text-emerald-600 dark:text-emerald-400">Saved.</p> : null}
      </div>
      {check.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {errText(check.error, "Plate check failed")}
        </p>
      ) : null}
      {check.data ? (
        <div className="flex flex-col gap-2" data-testid="plate-check-result">
          <p className={check.data.empty ? "text-sm text-emerald-600 dark:text-emerald-400" : "text-sm text-destructive"}>
            {check.data.empty ? "Empty" : "Not empty"} - {check.data.message} (threshold {check.data.threshold}%)
          </p>
          {check.data.preview ? (
            <img src={check.data.preview} alt="Annotated plate check" className="max-w-md rounded-md border" />
          ) : null}
        </div>
      ) : null}
      {diagnose.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {errText(diagnose.error, "Diagnose failed")}
        </p>
      ) : null}
      {diagnose.data ? (
        <p className={diagnose.data.ok ? "text-sm text-emerald-600 dark:text-emerald-400" : "text-sm text-destructive"}>
          Camera ({diagnose.data.camera_kind}): {diagnose.data.hint}
        </p>
      ) : null}
      <CalibrationWizard printer={printer} plateType={plateType.trim()} open={wizard} onOpenChange={setWizard} />
    </fieldset>
  );
}

function CalibrationWizard({
  printer,
  plateType,
  open,
  onOpenChange,
}: {
  printer: PrinterOut;
  plateType: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [step, setStep] = useState(0);
  const refs = usePlateReferences(printer.id, open);
  const capture = useCaptureReference(printer.id);
  const del = useDeleteReference(printer.id);
  const mine = (refs.data ?? []).filter((r) => r.plate_type === plateType);

  function close(next: boolean) {
    if (!next) setStep(0);
    onOpenChange(next);
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Calibrate plate check</DialogTitle>
          <DialogDescription>
            Plate type: {plateType || "(unspecified)"} - step {step + 1} of 3
          </DialogDescription>
        </DialogHeader>
        {step === 0 ? (
          <div className="flex flex-col gap-3 text-sm">
            <p>Remove everything from the build plate, close the door and make sure the chamber light is on.</p>
            <Button type="button" className="self-start" onClick={() => setStep(1)}>
              The plate is empty
            </Button>
          </div>
        ) : null}
        {step === 1 ? (
          <div className="flex flex-col gap-3 text-sm">
            <p>
              Capture two or three references (vary the lighting a little). Up to {MAX_REFERENCES} are kept; the
              oldest is replaced.
            </p>
            <div className="flex flex-wrap gap-2">
              {mine.map((r) => (
                <div key={r.id} className="flex flex-col items-center gap-1">
                  <img
                    src={referenceImageUrl(printer.id, r.id)}
                    alt={`Reference ${r.id}`}
                    className="h-20 rounded border"
                  />
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-label={`Delete reference ${r.id}`}
                    disabled={del.isPending}
                    onClick={() => del.mutate(r.id)}
                  >
                    Delete
                  </Button>
                </div>
              ))}
            </div>
            {capture.isError ? (
              <p role="alert" className="text-destructive">
                {errText(capture.error, "Capture failed")}
              </p>
            ) : null}
            <div className="flex gap-2">
              <Button
                type="button"
                disabled={capture.isPending}
                onClick={() => capture.mutate({ plate_type: plateType })}
              >
                {capture.isPending ? "Capturing..." : "Capture reference"}
              </Button>
              <Button type="button" variant="outline" disabled={mine.length === 0} onClick={() => setStep(2)}>
                Next
              </Button>
            </div>
          </div>
        ) : null}
        {step === 2 ? (
          <div className="flex flex-col gap-3 text-sm">
            <p>
              Calibrated with {mine.length} reference{mine.length === 1 ? "" : "s"}. Close this dialog, enable the
              check, save, and use Test to verify (with and without an object on the plate).
            </p>
            <Button type="button" className="self-start" onClick={() => close(false)}>
              Done
            </Button>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
