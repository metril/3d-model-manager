/**
 * Per-printer "check plate before print" (V4): enable switch, threshold and
 * plate type (stored in `printer.options.plate_check`), a calibration wizard
 * (capture empty-plate reference frames) and a Test button that runs
 * `POST /printers/{id}/check-plate` and shows the annotated preview. Also a
 * camera diagnose action.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

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
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const MAX_REFERENCES = 5;

type PlateCheckOptions = { enabled?: boolean; threshold?: number; plate_type?: string };

function errText(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.detail : fallback;
}

export function PrinterPlateCheckSection({ printer }: { printer: PrinterOut }) {
  const { t } = useTranslation();
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
      <legend className="px-1 text-sm font-medium">{t("settingsPlateCheck.title")}</legend>
      <p className="text-xs text-muted-foreground">
        {t("settingsPlateCheck.description")}
      </p>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="flex items-center gap-2 text-sm" htmlFor={`${id}-enabled`}>
          <input
            id={`${id}-enabled`}
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          {t("settingsPlateCheck.enable")}
        </label>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-threshold`}>{t("settingsPlateCheck.threshold")}</Label>
          <Input
            id={`${id}-threshold`}
            inputMode="decimal"
            value={threshold}
            aria-invalid={!thrValid}
            onChange={(e) => setThreshold(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-plate`}>{t("settingsPlateCheck.plateType")}</Label>
          <Input
            id={`${id}-plate`}
            value={plateType}
            maxLength={64}
            placeholder={t("settingsPlateCheck.platePlaceholder")}
            onChange={(e) => setPlateType(e.target.value)}
          />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" size="sm" onClick={save} disabled={update.isPending || !thrValid}>
          {update.isPending ? t("settingsPlateCheck.saving") : t("settingsPlateCheck.save")}
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={() => setWizard(true)}>
          {t("settingsPlateCheck.calibrate")}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={check.isPending || !thrValid}
          onClick={() => check.mutate({ plate_type: plateType.trim(), threshold: thr })}
        >
          {check.isPending ? t("settingsPlateCheck.checking") : t("settingsPlateCheck.test")}
        </Button>
        <Button type="button" size="sm" variant="ghost" disabled={diagnose.isPending} onClick={() => diagnose.mutate()}>
          {diagnose.isPending ? t("settingsPlateCheck.diagnosing") : t("settingsPlateCheck.diagnose")}
        </Button>
        {update.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {errText(update.error, t("settingsPlateCheck.saveError"))}
          </p>
        ) : null}
        {update.isSuccess ? <p className="text-sm text-success">{t("settingsPlateCheck.saved")}</p> : null}
      </div>
      {check.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {errText(check.error, t("settingsPlateCheck.checkFailed"))}
        </p>
      ) : null}
      {check.data ? (
        <div className="flex flex-col gap-2" data-testid="plate-check-result">
          <p className={check.data.empty ? "text-sm text-success" : "text-sm text-destructive"}>
            {check.data.empty ? t("settingsPlateCheck.empty") : t("settingsPlateCheck.notEmpty")} - {check.data.message} ({t("settingsPlateCheck.thresholdShort", { value: check.data.threshold })})
          </p>
          {check.data.preview ? (
            <img src={check.data.preview} alt={t("settingsPlateCheck.annotated")} className="max-w-md rounded-md border" />
          ) : null}
        </div>
      ) : null}
      {diagnose.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {errText(diagnose.error, t("settingsPlateCheck.diagnoseFailed"))}
        </p>
      ) : null}
      {diagnose.data ? (
        <p className={diagnose.data.ok ? "text-sm text-success" : "text-sm text-destructive"}>
          {t("settingsPlateCheck.cameraHint", { kind: diagnose.data.camera_kind, hint: diagnose.data.hint })}
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
  const { t } = useTranslation();
  const [deletingRef, setDeletingRef] = useState<number | null>(null);
  const mine = (refs.data ?? []).filter((r) => r.plate_type === plateType);

  function close(next: boolean) {
    if (!next) setStep(0);
    onOpenChange(next);
  }

  return (
    <FormDialog open={open} onOpenChange={close} dirty={step > 0} busy={capture.isPending || del.isPending} className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{t("settingsPlateCheck.wizardTitle")}</DialogTitle>
          <DialogDescription>
            {t("settingsPlateCheck.wizardStep", { type: plateType || t("settingsPlateCheck.unspecified"), step: step + 1 })}
          </DialogDescription>
        </DialogHeader>
        {step === 0 ? (
          <div className="flex flex-col gap-3 text-sm">
            <p>{t("settingsPlateCheck.step0")}</p>
            <Button type="button" className="self-start" onClick={() => setStep(1)}>
              {t("settingsPlateCheck.plateEmpty")}
            </Button>
          </div>
        ) : null}
        {step === 1 ? (
          <div className="flex flex-col gap-3 text-sm">
            <p>
              {t("settingsPlateCheck.step1", { max: MAX_REFERENCES })}
            </p>
            <div className="flex flex-wrap gap-2">
              {mine.map((r) => (
                <div key={r.id} className="flex flex-col items-center gap-1">
                  <img
                    src={referenceImageUrl(printer.id, r.id)}
                    alt={t("settingsPlateCheck.reference", { id: r.id })}
                    className="h-20 rounded border"
                  />
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-label={t("settingsPlateCheck.deleteReference", { id: r.id })}
                    disabled={del.isPending}
                    onClick={() => setDeletingRef(r.id)}
                  >
                    {t("common.delete")}
                  </Button>
                </div>
              ))}
            </div>
            <ConfirmDialog
              open={deletingRef != null}
              onOpenChange={(open) => !open && setDeletingRef(null)}
              title={t("plateRef.deleteTitle")}
              description={t("plateRef.deleteDesc")}
              confirmLabel={t("common.delete")}
              cancelLabel={t("common.cancel")}
              destructive
              onConfirm={() => deletingRef != null && del.mutate(deletingRef)}
            />
            {capture.isError ? (
              <p role="alert" className="text-destructive">
                {errText(capture.error, t("settingsPlateCheck.captureFailed"))}
              </p>
            ) : null}
            <div className="flex gap-2">
              <Button
                type="button"
                disabled={capture.isPending}
                onClick={() => capture.mutate({ plate_type: plateType })}
              >
                {capture.isPending ? t("settingsPlateCheck.capturing") : t("settingsPlateCheck.capture")}
              </Button>
              <Button type="button" variant="outline" disabled={mine.length === 0} onClick={() => setStep(2)}>
                {t("settingsPlateCheck.next")}
              </Button>
            </div>
          </div>
        ) : null}
        {step === 2 ? (
          <div className="flex flex-col gap-3 text-sm">
            <p>
              {t("settingsPlateCheck.calibrated", { count: mine.length })}
            </p>
            <Button type="button" className="self-start" onClick={() => close(false)}>
              {t("settingsPlateCheck.done")}
            </Button>
          </div>
        ) : null}
    </FormDialog>
  );
}
