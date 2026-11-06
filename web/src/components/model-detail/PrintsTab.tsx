/**
 * Prints tab (Branch 5 Task 2): a user-entered log of print attempts,
 * distinct from the print queue's worklist (`api/queue.ts`) and print-jobs'
 * live send-to-printer telemetry (M4). Shape mirrors `NotesTab.tsx`
 * (composer at top, list below) and `NoteItem.tsx` (inline edit/delete,
 * delete gated behind `ConfirmDialog`).
 */
import { useTranslation } from "react-i18next";
import { useId, useState, type FormEvent } from "react";
import { PlusIcon } from "lucide-react";
import { toast } from "sonner";

import { useMaterials } from "@/api/materials";
import { useLogPrint, usePatchPrint, useDeletePrint, usePrints } from "@/api/prints";
import { usePrinters } from "@/api/printers";
import { useQueue, useRemoveQueueEntry } from "@/api/queue";
import type {
  MaterialOut,
  ModelDetail,
  PrintCreateIn,
  PrintEntry,
  PrintPatchIn,
  PrintResult,
  PrinterOut,
} from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { FilamentChip } from "@/components/ui/filament-chip";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { formatCost, formatDateTime, toDatetimeLocalValue } from "@/lib/format";

/** Sentinel `Select` value for "no printer" -- Radix `SelectItem` reserves
 * an empty-string value for "no selection", so it can't represent `null`
 * directly. */
const NO_PRINTER = "__none__";

/** Same sentinel pattern as `NO_PRINTER`, for the material `Select`: no
 * material picked yet, or explicitly "Other..." (use the freeform filament
 * text field instead of a saved material). Both keep the filament field
 * visible; only picking a real material hides it. */
const NO_MATERIAL = "__none__";
const OTHER_MATERIAL = "__other__";

const RESULT_LABELS: Record<PrintResult, string> = {
  success: "mdPrints.success",
  fail: "mdPrints.fail",
  partial: "mdPrints.partial",
};

/** `fail` reuses the shared `destructive` Badge variant; `success`/`partial`
 * layer emerald/amber onto `outline` since neither exists as a Badge variant
 * of its own -- mirrors `DiffView.tsx`'s per-section coloring. */
const RESULT_BADGE: Record<PrintResult, { variant: "destructive" | "outline"; className?: string }> = {
  success: { variant: "outline", className: "bg-success/15 text-success" },
  fail: { variant: "destructive" },
  partial: { variant: "outline", className: "bg-warning/15 text-warning" },
};

interface PrintDraft {
  printedAt: string;
  printerName: string;
  result: PrintResult;
  materialId: string;
  filament: string;
  filamentG: string;
  durationMin: string;
  notes: string;
}

function emptyDraft(): PrintDraft {
  return {
    printedAt: toDatetimeLocalValue(new Date()),
    printerName: NO_PRINTER,
    result: "success",
    materialId: NO_MATERIAL,
    filament: "",
    filamentG: "",
    durationMin: "",
    notes: "",
  };
}

function draftFromEntry(entry: PrintEntry): PrintDraft {
  return {
    printedAt: toDatetimeLocalValue(new Date(entry.printed_at)),
    printerName: entry.printer_name ?? NO_PRINTER,
    result: entry.result,
    materialId: entry.material_id != null ? String(entry.material_id) : NO_MATERIAL,
    filament: entry.filament ?? "",
    filamentG: entry.filament_g != null ? String(entry.filament_g) : "",
    durationMin: entry.duration_min != null ? String(entry.duration_min) : "",
    notes: entry.notes ?? "",
  };
}

/** Shared field set for both the log composer and a row's inline edit form.
 * Each instance mints its own id namespace (`useId`) so multiple copies on
 * the page at once -- the composer plus any row being edited -- never
 * collide. */
function PrintFields({
  draft,
  onFieldChange,
  printers,
  materials,
}: {
  draft: PrintDraft;
  onFieldChange: <K extends keyof PrintDraft>(key: K, value: PrintDraft[K]) => void;
  printers: PrinterOut[];
  materials: MaterialOut[];
}) {
  const id = useId();
  const { t } = useTranslation();

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-printed-at`}>{t("mdPrints.printedAt")}</Label>
        <Input
          id={`${id}-printed-at`}
          type="datetime-local"
          value={draft.printedAt}
          onChange={(event) => onFieldChange("printedAt", event.target.value)}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-printer`}>{t("mdPrints.printer")}</Label>
        <Select
          aria-label={t("mdPrints.printer")}
          value={draft.printerName}
          onValueChange={(value) => onFieldChange("printerName", value)}
        >
          <SelectTrigger id={`${id}-printer`} aria-label={t("mdPrints.printer")} className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NO_PRINTER}>{t("mdPrints.none")}</SelectItem>
            {printers.map((printer) => (
              <SelectItem key={printer.id} value={printer.name}>
                {printer.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-result`}>{t("mdPrints.result")}</Label>
        <Select
          aria-label={t("mdPrints.result")}
          value={draft.result}
          onValueChange={(value) => onFieldChange("result", value as PrintResult)}
        >
          <SelectTrigger id={`${id}-result`} aria-label={t("mdPrints.result")} className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(Object.keys(RESULT_LABELS) as PrintResult[]).map((result) => (
              <SelectItem key={result} value={result}>
                {t(RESULT_LABELS[result])}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-material`}>{t("mdPrints.material")}</Label>
        <Select
          aria-label={t("mdPrints.material")}
          value={draft.materialId}
          onValueChange={(value) => onFieldChange("materialId", value)}
        >
          <SelectTrigger id={`${id}-material`} aria-label={t("mdPrints.material")} className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NO_MATERIAL}>{t("mdPrints.none")}</SelectItem>
            {materials.map((material) => (
              <SelectItem key={material.id} value={String(material.id)}>
                {material.name}
              </SelectItem>
            ))}
            <SelectItem value={OTHER_MATERIAL}>{t("mdPrints.other")}</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {(draft.materialId === NO_MATERIAL || draft.materialId === OTHER_MATERIAL) && (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-filament`}>{t("mdPrints.filament")}</Label>
          <Input
            id={`${id}-filament`}
            placeholder={t("mdPrints.filamentPh")}
            value={draft.filament}
            onChange={(event) => onFieldChange("filament", event.target.value)}
          />
        </div>
      )}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-duration`}>{t("mdPrints.duration")}</Label>
        <Input
          id={`${id}-duration`}
          type="number"
          min={0}
          value={draft.durationMin}
          onChange={(event) => onFieldChange("durationMin", event.target.value)}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-filament-g`}>{t("mdPrints.filamentUsed")}</Label>
        <Input
          id={`${id}-filament-g`}
          type="number"
          min={0}
          step="any"
          value={draft.filamentG}
          onChange={(event) => onFieldChange("filamentG", event.target.value)}
        />
      </div>
      <div className="flex flex-col gap-1.5 sm:col-span-2">
        <Label htmlFor={`${id}-notes`}>{t("mdPrints.notes")}</Label>
        <Textarea
          id={`${id}-notes`}
          rows={2}
          value={draft.notes}
          onChange={(event) => onFieldChange("notes", event.target.value)}
        />
      </div>
    </div>
  );
}

/** Top-of-tab composer -- logs a new print attempt. The `printed_at` field
 * defaults to now but is only sent if the user actually touches it, letting
 * the server's own clock be authoritative for the common case (SPEC:
 * "omit if untouched -> server now"). */
function PrintComposer({
  modelId,
  slug,
  printers,
  materials,
  onLogged,
}: {
  modelId: number;
  slug: string;
  printers: PrinterOut[];
  materials: MaterialOut[];
  onLogged: (result: PrintResult) => void;
}) {
  const [draft, setDraft] = useState<PrintDraft>(emptyDraft);
  const [printedAtTouched, setPrintedAtTouched] = useState(false);
  const logPrint = useLogPrint(modelId, slug);
  const { t } = useTranslation();

  function handleFieldChange<K extends keyof PrintDraft>(key: K, value: PrintDraft[K]) {
    setDraft((prev) => ({ ...prev, [key]: value }));
    if (key === "printedAt") setPrintedAtTouched(true);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload: PrintCreateIn = {
      printer_name: draft.printerName === NO_PRINTER ? null : draft.printerName,
      result: draft.result,
      material_id:
        draft.materialId !== NO_MATERIAL && draft.materialId !== OTHER_MATERIAL ? Number(draft.materialId) : null,
      filament: draft.filament.trim() || null,
      filament_g: draft.filamentG.trim() === "" ? null : Number(draft.filamentG),
      duration_min: draft.durationMin.trim() === "" ? null : Number(draft.durationMin),
      notes: draft.notes.trim() || null,
    };
    if (printedAtTouched) payload.printed_at = new Date(draft.printedAt).toISOString();

    logPrint.mutate(payload, {
      onSuccess: () => {
        setDraft(emptyDraft());
        setPrintedAtTouched(false);
        onLogged(draft.result);
      },
    });
  }

  return (
    <Card>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-3">
          <h3 className="text-sm font-semibold">{t("mdPrints.logAPrint")}</h3>
          <PrintFields draft={draft} onFieldChange={handleFieldChange} printers={printers} materials={materials} />
          <Button type="submit" size="sm" disabled={logPrint.isPending}>
            {t("mdPrints.logPrint")}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

/** One print-log row -- view/edit/delete, modeled on `NoteItem.tsx`. Unlike
 * `NoteItem`, several fields can change independently, so the edit form
 * tracks which ones the user actually touched and only sends those in the
 * `PATCH` body (backend's `exclude_unset` patch semantics). */
function PrintRow({
  entry,
  printers,
  materials,
  isSaving,
  onSave,
  onDelete,
}: {
  entry: PrintEntry;
  printers: PrinterOut[];
  materials: MaterialOut[];
  isSaving: boolean;
  onSave: (patch: PrintPatchIn, onSuccess: () => void) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const { t } = useTranslation();
  const [draft, setDraft] = useState<PrintDraft>(() => draftFromEntry(entry));
  const [touched, setTouched] = useState<Partial<Record<keyof PrintDraft, true>>>({});

  function handleFieldChange<K extends keyof PrintDraft>(key: K, value: PrintDraft[K]) {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setTouched((prev) => ({ ...prev, [key]: true }));
  }

  function handleSave() {
    const patch: PrintPatchIn = {};
    if (touched.printedAt) patch.printed_at = new Date(draft.printedAt).toISOString();
    if (touched.printerName) patch.printer_name = draft.printerName === NO_PRINTER ? null : draft.printerName;
    if (touched.result) patch.result = draft.result;
    if (touched.materialId) {
      patch.material_id =
        draft.materialId !== NO_MATERIAL && draft.materialId !== OTHER_MATERIAL ? Number(draft.materialId) : null;
    }
    if (touched.filament) patch.filament = draft.filament.trim() || null;
    if (touched.filamentG) patch.filament_g = draft.filamentG.trim() === "" ? null : Number(draft.filamentG);
    if (touched.durationMin) patch.duration_min = draft.durationMin.trim() === "" ? null : Number(draft.durationMin);
    if (touched.notes) patch.notes = draft.notes.trim() || null;
    onSave(patch, () => setEditing(false));
  }

  function handleCancel() {
    setDraft(draftFromEntry(entry));
    setTouched({});
    setEditing(false);
  }

  if (editing) {
    return (
      <li className="rounded-lg border border-border p-3">
        <PrintFields draft={draft} onFieldChange={handleFieldChange} printers={printers} materials={materials} />
        <div className="mt-3 flex gap-2">
          <Button type="button" size="sm" disabled={isSaving} onClick={handleSave}>
            {t("common.save")}
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={handleCancel}>
            {t("common.cancel")}
          </Button>
        </div>
      </li>
    );
  }

  const badge = RESULT_BADGE[entry.result];

  return (
    <li className="rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="font-medium">{formatDateTime(entry.printed_at)}</span>
          <Badge variant={badge.variant} className={badge.className}>
            {t(RESULT_LABELS[entry.result])}
          </Badge>
          {entry.printer_name ? <span className="text-muted-foreground">{entry.printer_name}</span> : null}
          {entry.material ? (
            <FilamentChip color={entry.material.color ?? undefined} material={entry.material.name} size="sm" />
          ) : entry.filament ? (
            <span className="text-muted-foreground">{entry.filament}</span>
          ) : null}
          {entry.filament_g != null ? (
            <span className="text-muted-foreground">{entry.filament_g} g</span>
          ) : null}
          {entry.duration_min != null ? (
            <span className="text-muted-foreground">{t("mdPrints.minutes", { n: entry.duration_min })}</span>
          ) : null}
          {entry.total_cost != null ? (
            <span className="text-muted-foreground">{t("mdPrints.estCost", { cost: formatCost(entry.total_cost) })}</span>
          ) : null}
        </div>
        <div className="flex shrink-0 gap-3 text-xs">
          <button type="button" className="hover:underline" onClick={() => setEditing(true)}>
            {t("common.edit")}
          </button>
          <ConfirmDialog
            trigger={
              <button type="button" className="hover:underline">
                {t("common.delete")}
              </button>
            }
            title={t("mdPrints.deleteTitle")}
            confirmLabel={t("common.delete")}
            destructive
            onConfirm={onDelete}
          />
        </div>
      </div>
      {entry.notes ? <p className="mt-1 text-sm text-muted-foreground">{entry.notes}</p> : null}
    </li>
  );
}

export function PrintsTab({ model }: { model: ModelDetail }) {
  const printsQuery = usePrints(model.id);
  const { t } = useTranslation();
  const printersQuery = usePrinters();
  const materialsQuery = useMaterials();
  const queueQuery = useQueue();
  const patchPrint = usePatchPrint(model.id, model.slug);
  const deletePrint = useDeletePrint(model.id, model.slug);
  const removeQueueEntry = useRemoveQueueEntry();
  // R13a wireframe: history shows first -- the composer is a collapsed
  // header toggle rather than always-expanded, closing itself again once a
  // print actually logs.
  const [composerOpen, setComposerOpen] = useState(false);

  const prints = printsQuery.data ?? [];
  const printers = printersQuery.data ?? [];
  const materials = materialsQuery.data ?? [];
  const queueEntry = queueQuery.data?.find((entry) => entry.model_id === model.id);

  // Offers to clear the model off the print queue after a successful log --
  // never automatic (SPEC: "Never auto-remove"). Folded into the same
  // success toast as an action button rather than a separate confirm step.
  function handleLogged(result: PrintResult) {
    if (result === "success" && queueEntry) {
      const entryId = queueEntry.id;
      toast.success(t("mdPrints.logged"), {
        action: {
          label: t("mdPrints.removeFromQueue"),
          onClick: () =>
            removeQueueEntry.mutate(entryId, {
              onSuccess: () => toast.success(t("mdPrints.removedFromQueue")),
            }),
        },
      });
    } else {
      toast.success(t("mdPrints.logged"));
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button type="button" variant="outline" size="sm" onClick={() => setComposerOpen((prev) => !prev)}>
          <PlusIcon className="size-4" />
          {t("mdPrints.logAPrint")}
        </Button>
      </div>

      {composerOpen && (
        <PrintComposer
          modelId={model.id}
          slug={model.slug}
          printers={printers}
          materials={materials}
          onLogged={(result) => {
            handleLogged(result);
            setComposerOpen(false);
          }}
        />
      )}

      {prints.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("mdPrints.empty")}</p>
      ) : (
        <ul className="space-y-3">
          {prints.map((entry) => (
            <PrintRow
              key={entry.id}
              entry={entry}
              printers={printers}
              materials={materials}
              isSaving={patchPrint.isPending}
              onSave={(patch, onSuccess) => patchPrint.mutate({ id: entry.id, patch }, { onSuccess })}
              onDelete={() => deletePrint.mutate(entry.id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
