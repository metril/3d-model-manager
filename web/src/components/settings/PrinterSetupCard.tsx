/**
 * Printer setup wizard on Settings (M4 Task 8): gated on the
 * `printer_enabled` feature flag (`GET /features`); when on, lists
 * configured printers with an edit form each (Test connection, Save,
 * confirm-gated Delete) plus an "Add a printer" form. Mirrors
 * `StorageSettingsCard`'s draft-state + pending-disable + `role="alert"`
 * error conventions, and its masked-secret UX for `access_code`:
 * `PrinterOut` never carries the code (only `access_code_set`), so the
 * input always starts blank -- typing a value sends it, leaving it blank
 * means "keep the stored one" (PATCH only).
 *
 * Round 8 T1 hardening: the serial is now REQUIRED (server-side too --
 * `PrinterCreate`/`PrinterUpdate` reject a blank/null serial), and every
 * other create field is checked client-side before the request ever goes
 * out -- a "Detect" button beside the Serial input reads the serial
 * straight off the printer's TLS cert (`POST /printers/detect-serial`) so
 * the user doesn't have to hunt for it on the printer's screen.
 */
import { useEffect, useState } from "react";
import { Trash2Icon } from "lucide-react";

import { ApiError } from "@/api/client";
import { useFeatures } from "@/api/features";
import { useDiagnostic, useDiscover } from "@/api/printerOps";
import {
  useCreatePrinter,
  useDeletePrinter,
  useDetectSerial,
  usePrinters,
  useTestPrinter,
  useUpdatePrinter,
} from "@/api/printers";
import type { DiagnosticCheck, DiscoveredPrinter, PrinterOut, PrinterUpdate } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DiagnosticChecks, DiagnosticModal } from "@/components/print/modals";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PrinterCameraSection } from "@/components/settings/PrinterCameraSection";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { PRINTER_MODELS } from "@/lib/printerState";

interface PrinterDraft {
  name: string;
  host: string;
  serial: string;
  model: string;
  access_code: string;
  buildVolumeX: string;
  buildVolumeY: string;
  buildVolumeZ: string;
}

// `buildVolume` isn't a `PrinterDraft` key -- the partial-fill validation
// error applies to all three build-volume fields together, not any one.
type DraftErrors = Partial<Record<keyof PrinterDraft, string>> & { buildVolume?: string };

function emptyDraft(): PrinterDraft {
  // Blank build-volume fields mean "don't send `build_volume_mm`" on
  // create -- the server seeds it from a model->volume map instead.
  return {
    name: "",
    host: "",
    serial: "",
    model: "",
    access_code: "",
    buildVolumeX: "",
    buildVolumeY: "",
    buildVolumeZ: "",
  };
}

/** A saved printer's `access_code` is never returned by the API (only
 * `access_code_set`) -- the field always seeds blank, which is what "leave
 * unchanged" looks like on save (mirrors `StorageBackendForm.seedDraft`). */
function seedDraft(printer: PrinterOut): PrinterDraft {
  return {
    name: printer.name,
    host: printer.host,
    serial: printer.serial,
    model: printer.model ?? "",
    access_code: "",
    buildVolumeX: printer.build_volume_mm?.x != null ? String(printer.build_volume_mm.x) : "",
    buildVolumeY: printer.build_volume_mm?.y != null ? String(printer.build_volume_mm.y) : "",
    buildVolumeZ: printer.build_volume_mm?.z != null ? String(printer.build_volume_mm.z) : "",
  };
}

export function PrinterSetupCard() {
  const features = useFeatures();

  if (features.isLoading) return <Skeleton className="h-48 w-full rounded-xl" />;

  if (!features.data?.printer_enabled) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Printer setup</CardTitle>
          <CardDescription>Printer integration is off. Enable it above to add and manage printers.</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return <EnabledPrinterSetupCard />;
}

type Fill = { seq: number; values: Partial<PrinterDraft> };

/** Default scan target: the /24 of the first saved printer, else blank (server picks). */
function defaultCidr(printers: PrinterOut[]): string {
  const m = printers[0]?.host.match(/^(\d+\.\d+\.\d+)\.\d+$/);
  return m ? `${m[1]}.0/24` : "";
}

function DiscoverSection({
  defaultValue,
  onPick,
}: {
  defaultValue: string;
  onPick: (d: DiscoveredPrinter) => void;
}) {
  const [cidr, setCidr] = useState<string | null>(null);
  const discover = useDiscover();
  const value = cidr ?? defaultValue;
  return (
    <div className="space-y-2 rounded-lg border border-dashed border-border p-3">
      <h2 className="text-sm font-semibold text-foreground">Discover printers</h2>
      <div className="flex gap-1.5">
        <Input
          aria-label="Subnet"
          value={value}
          placeholder="192.168.1.0/24 (auto)"
          onChange={(e) => setCidr(e.target.value)}
        />
        <Button
          type="button"
          variant="outline"
          disabled={discover.isPending}
          onClick={() => discover.mutate(value.trim() || null)}
        >
          {discover.isPending ? "Scanning..." : "Scan"}
        </Button>
      </div>
      {discover.isError ? (
        <p role="alert" className="text-xs text-destructive">
          {discover.error instanceof ApiError ? discover.error.detail : "Scan failed"}
        </p>
      ) : null}
      {discover.data && discover.data.length === 0 ? (
        <p className="text-xs text-muted-foreground">No printers found.</p>
      ) : null}
      <ul className="space-y-1">
        {(discover.data ?? []).map((d) => (
          <li key={`${d.host}-${d.serial}`}>
            <button
              type="button"
              className="w-full rounded-md border px-2 py-1 text-left text-sm hover:bg-muted"
              onClick={() => onPick(d)}
            >
              {d.model_hint ?? "Bambu printer"} · {d.host} · {d.serial}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function EnabledPrinterSetupCard() {
  const printers = usePrinters();
  const [fill, setFill] = useState<Fill | null>(null);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Printer setup</CardTitle>
        <CardDescription>Add and manage the printers this app can send sliced files to.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {printers.isLoading ? (
          <Skeleton className="h-24 w-full rounded-lg" />
        ) : printers.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {printers.error instanceof ApiError ? printers.error.detail : "Couldn't load printers."}
          </p>
        ) : (
          (printers.data ?? []).map((printer) => <PrinterEditor key={printer.id} printer={printer} />)
        )}
        <div className="space-y-3 border-t pt-4">
          <h2 className="text-sm font-semibold text-foreground">Add a printer</h2>
          <DiscoverSection
            defaultValue={defaultCidr(printers.data ?? [])}
            onPick={(d) =>
              setFill((f) => ({
                seq: (f?.seq ?? 0) + 1,
                values: { name: d.model_hint ?? "Bambu printer", host: d.host, serial: d.serial, model: d.model_hint ?? "" },
              }))
            }
          />
          <PrinterEditor fill={fill} />
        </div>
      </CardContent>
    </Card>
  );
}

function PrinterEditor({ printer, fill }: { printer?: PrinterOut; fill?: Fill | null }) {
  const [draft, setDraft] = useState<PrinterDraft>(() => (printer ? seedDraft(printer) : emptyDraft()));
  const [errors, setErrors] = useState<DraftErrors>({});
  const [diagOpen, setDiagOpen] = useState(false);
  const [failures, setFailures] = useState<DiagnosticCheck[] | null>(null);
  const preflight = useDiagnostic();

  useEffect(() => {
    if (fill) setDraft((prev) => ({ ...prev, ...fill.values }));
  }, [fill]);

  const createPrinter = useCreatePrinter();
  const updatePrinter = useUpdatePrinter(printer?.id ?? -1);
  const deletePrinter = useDeletePrinter();
  const testPrinter = useTestPrinter(printer?.id ?? -1);
  const detectSerial = useDetectSerial();

  const saveMutation = printer ? updatePrinter : createPrinter;
  const busy = createPrinter.isPending || updatePrinter.isPending || deletePrinter.isPending;
  const idPrefix = printer ? `printer-${printer.id}` : "printer-new";

  function setField<K extends keyof PrinterDraft>(key: K, value: PrinterDraft[K]) {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setErrors((prev) => (prev[key] ? { ...prev, [key]: undefined } : prev));
  }

  /** Serial is required on both create and update (mirrors the backend's
   * `PrinterCreate`/`PrinterUpdate` serial validator); name/host/access_code
   * are only required on create -- an existing printer's row already has
   * them, and a blank `access_code` on update means "keep the stored one".
   * Build volume is optional, but must be all-three-or-none: a partial fill
   * can't be turned into a valid `{x,y,z}` body. */
  function validate(): boolean {
    const next: DraftErrors = {};
    if (draft.serial.trim() === "") next.serial = "Serial is required.";
    if (!printer) {
      if (draft.name.trim() === "") next.name = "Name is required.";
      if (draft.host.trim() === "") next.host = "Host is required.";
      if (draft.access_code.trim() === "") next.access_code = "Access code is required.";
    }
    const volumeFilled = [draft.buildVolumeX, draft.buildVolumeY, draft.buildVolumeZ].filter(
      (v) => v.trim() !== "",
    ).length;
    if (volumeFilled > 0 && volumeFilled < 3) {
      next.buildVolume = "Enter all three dimensions, or leave all blank.";
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  /** `undefined` when all three fields are blank (create: let the server
   * seed from its model->volume map; update: leave the stored value
   * untouched) -- `validate()` above already blocked a partial fill. */
  function buildVolumeBody(): { x: number; y: number; z: number } | undefined {
    if (draft.buildVolumeX.trim() === "" && draft.buildVolumeY.trim() === "" && draft.buildVolumeZ.trim() === "") {
      return undefined;
    }
    return { x: Number(draft.buildVolumeX), y: Number(draft.buildVolumeY), z: Number(draft.buildVolumeZ) };
  }

  function detect() {
    detectSerial.mutate(
      { host: draft.host },
      {
        onSuccess: (result) => {
          if (result.serial) setField("serial", result.serial);
        },
      },
    );
  }

  /** New printers get a diagnostic preflight; failures offer Back / Save anyway
   * instead of blocking, and a failed/unreachable preflight never blocks. */
  function save() {
    if (!validate()) return;
    if (printer) return doSave();
    preflight.mutate(
      { adhoc: { host: draft.host, serial: draft.serial, access_code: draft.access_code, model: draft.model || null } },
      {
        onSuccess: (r) => {
          const bad = (r?.checks ?? []).filter((c) => c.status === "fail");
          if (bad.length > 0) setFailures(bad);
          else doSave();
        },
        onError: () => doSave(),
      },
    );
  }

  function doSave() {
    setFailures(null);
    const code = draft.access_code.trim();
    const buildVolume = buildVolumeBody();
    if (printer) {
      // PATCH: a blank access_code means "keep the stored one" -- the key
      // is left out of the body entirely so the backend's `exclude_unset`
      // handling never touches `access_code_enc`. Same idea for
      // `build_volume_mm`: omitted entirely when left blank.
      const body: PrinterUpdate = {
        name: draft.name,
        host: draft.host,
        serial: draft.serial,
        model: draft.model.trim() === "" ? null : draft.model,
      };
      if (code !== "") body.access_code = code;
      if (buildVolume) body.build_volume_mm = buildVolume;
      updatePrinter.mutate(body, { onSuccess: () => setField("access_code", "") });
    } else {
      // POST: `access_code` is a required field on `PrinterCreate` (a new
      // printer has no stored code to fall back to) -- `validate()` above
      // already blocked a blank one client-side, so this always sends a
      // real value. `build_volume_mm` is omitted when blank so the server's
      // model->volume seed logic applies.
      createPrinter.mutate(
        {
          name: draft.name,
          host: draft.host,
          serial: draft.serial,
          model: draft.model.trim() === "" ? null : draft.model,
          access_code: code,
          ...(buildVolume ? { build_volume_mm: buildVolume } : {}),
        },
        { onSuccess: () => setDraft(emptyDraft()) },
      );
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-border p-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${idPrefix}-name`}>Name</Label>
          <Input
            id={`${idPrefix}-name`}
            value={draft.name}
            onChange={(e) => setField("name", e.target.value)}
            disabled={busy}
            aria-invalid={Boolean(errors.name)}
          />
          {errors.name ? (
            <p role="alert" className="text-xs text-destructive">
              {errors.name}
            </p>
          ) : null}
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${idPrefix}-host`}>Host</Label>
          <Input
            id={`${idPrefix}-host`}
            value={draft.host}
            placeholder="192.168.1.50"
            onChange={(e) => setField("host", e.target.value)}
            disabled={busy}
            aria-invalid={Boolean(errors.host)}
          />
          {errors.host ? (
            <p role="alert" className="text-xs text-destructive">
              {errors.host}
            </p>
          ) : null}
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${idPrefix}-serial`}>Serial</Label>
          <div className="flex gap-1.5">
            <Input
              id={`${idPrefix}-serial`}
              value={draft.serial}
              onChange={(e) => setField("serial", e.target.value)}
              disabled={busy}
              aria-invalid={Boolean(errors.serial)}
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busy || detectSerial.isPending || draft.host.trim() === ""}
              onClick={detect}
            >
              {detectSerial.isPending ? "Detecting..." : "Detect"}
            </Button>
          </div>
          {errors.serial ? (
            <p role="alert" className="text-xs text-destructive">
              {errors.serial}
            </p>
          ) : null}
          {detectSerial.isError ? (
            <p role="alert" className="text-xs text-destructive">
              {detectSerial.error instanceof ApiError ? detectSerial.error.detail : "Could not detect the serial"}
            </p>
          ) : null}
          {detectSerial.data ? (
            <p
              role={detectSerial.data.serial ? undefined : "alert"}
              className={
                detectSerial.data.serial ? "text-xs text-emerald-600 dark:text-emerald-400" : "text-xs text-destructive"
              }
            >
              {detectSerial.data.detail}
            </p>
          ) : null}
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${idPrefix}-model`}>Model</Label>
          <select
            id={`${idPrefix}-model`}
            value={draft.model}
            onChange={(e) => setField("model", e.target.value)}
            disabled={busy}
            className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
          >
            <option value="">Select a model</option>
            {draft.model !== "" && !PRINTER_MODELS.some((g) => g.models.some((o) => o.value === draft.model)) ? (
              <option value={draft.model}>{draft.model}</option>
            ) : null}
            {PRINTER_MODELS.map((g) => (
              <optgroup key={g.group} label={g.group}>
                {g.models.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1.5 sm:col-span-2">
          <Label htmlFor={`${idPrefix}-access-code`}>Access code</Label>
          <Input
            id={`${idPrefix}-access-code`}
            type="password"
            value={draft.access_code}
            placeholder={printer?.access_code_set ? "•• (unchanged)" : "LAN access code"}
            onChange={(e) => setField("access_code", e.target.value)}
            disabled={busy}
            autoComplete="new-password"
            aria-invalid={Boolean(errors.access_code)}
          />
          {errors.access_code ? (
            <p role="alert" className="text-xs text-destructive">
              {errors.access_code}
            </p>
          ) : null}
        </div>
        <div className="flex flex-col gap-1.5 sm:col-span-2">
          <span className="text-sm font-medium text-foreground">Build volume (mm)</span>
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`${idPrefix}-volume-x`}>Build volume X (mm)</Label>
              <Input
                id={`${idPrefix}-volume-x`}
                type="number"
                value={draft.buildVolumeX}
                onChange={(e) => setField("buildVolumeX", e.target.value)}
                disabled={busy}
                aria-invalid={Boolean(errors.buildVolume)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`${idPrefix}-volume-y`}>Y (mm)</Label>
              <Input
                id={`${idPrefix}-volume-y`}
                type="number"
                value={draft.buildVolumeY}
                onChange={(e) => setField("buildVolumeY", e.target.value)}
                disabled={busy}
                aria-invalid={Boolean(errors.buildVolume)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`${idPrefix}-volume-z`}>Z (mm)</Label>
              <Input
                id={`${idPrefix}-volume-z`}
                type="number"
                value={draft.buildVolumeZ}
                onChange={(e) => setField("buildVolumeZ", e.target.value)}
                disabled={busy}
                aria-invalid={Boolean(errors.buildVolume)}
              />
            </div>
          </div>
          {errors.buildVolume ? (
            <p role="alert" className="text-xs text-destructive">
              {errors.buildVolume}
            </p>
          ) : null}
        </div>
      </div>

      {printer ? <PrinterCameraSection printer={printer} /> : null}

      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" onClick={save} disabled={busy || preflight.isPending || failures !== null}>
          {saveMutation.isPending ? "Saving..." : preflight.isPending ? "Checking..." : printer ? "Save changes" : "Add printer"}
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={busy || (printer ? false : !draft.host.trim() || !draft.serial.trim() || !draft.access_code.trim())}
          onClick={() => setDiagOpen(true)}
        >
          Run diagnostic
        </Button>
        {printer ? (
          <>
            <Button
              type="button"
              variant="outline"
              disabled={busy || testPrinter.isPending}
              onClick={() => testPrinter.mutate()}
            >
              {testPrinter.isPending ? "Testing..." : "Test connection"}
            </Button>
            <ConfirmDialog
              trigger={
                <Button type="button" variant="destructive" size="sm" disabled={busy}>
                  <Trash2Icon className="size-3.5" />
                  Delete
                </Button>
              }
              title={`Delete ${printer.name}?`}
              description="This also removes its print job history. This cannot be undone."
              confirmLabel="Delete"
              destructive
              onConfirm={() => deletePrinter.mutate(printer.id)}
            />
          </>
        ) : null}
      </div>

      {failures ? (
        <div role="alert" className="space-y-2 rounded-md border border-destructive/40 p-3">
          <p className="text-sm font-medium text-destructive">Connection checks failed</p>
          <DiagnosticChecks checks={failures} />
          <div className="flex gap-2">
            <Button type="button" variant="outline" size="sm" onClick={() => setFailures(null)}>
              Back
            </Button>
            <Button type="button" size="sm" onClick={doSave}>
              Save anyway
            </Button>
          </div>
        </div>
      ) : null}
      <DiagnosticModal
        open={diagOpen}
        onOpenChange={setDiagOpen}
        printerId={printer && draft.access_code.trim() === "" ? printer.id : undefined}
        adhoc={{ host: draft.host, serial: draft.serial, access_code: draft.access_code, model: draft.model || null }}
      />

      {saveMutation.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {saveMutation.error instanceof ApiError ? saveMutation.error.detail : "Could not save printer"}
        </p>
      ) : null}
      {saveMutation.isSuccess ? <p className="text-sm text-emerald-600 dark:text-emerald-400">Saved.</p> : null}

      {deletePrinter.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {deletePrinter.error instanceof ApiError ? deletePrinter.error.detail : "Could not delete printer"}
        </p>
      ) : null}

      {testPrinter.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {testPrinter.error instanceof ApiError ? testPrinter.error.detail : "Could not test connection"}
        </p>
      ) : null}
      {testPrinter.data ? (
        <p role={testPrinter.data.ok ? undefined : "alert"} className={testPrinter.data.ok ? "text-sm text-emerald-600 dark:text-emerald-400" : "text-sm text-destructive"}>
          {testPrinter.data.detail}
          {testPrinter.data.gcode_state ? ` (state: ${testPrinter.data.gcode_state})` : ""}
        </p>
      ) : null}
    </div>
  );
}
