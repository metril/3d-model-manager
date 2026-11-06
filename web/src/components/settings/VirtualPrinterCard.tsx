/**
 * Virtual printer (Q11): a fake LAN Bambu printer slicers can "print" to.
 * Lists/creates/edits/deletes `virtual_printers` rows and shows each one's
 * serial + access code with Bambu Studio setup steps. Rendered on the
 * Settings -> Printer tab only when `GET /features` reports
 * `virtual_printer_enabled` (the `virtual-printer` compose service is opt-in).
 */
import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import type { TFunction } from "i18next";

import { ApiError } from "@/api/client";
import { usePrinters } from "@/api/printers";
import type { VirtualPrinter, VirtualPrinterMode } from "@/api/types";
import {
  useCreateVirtualPrinter,
  useDeleteVirtualPrinter,
  useUpdateVirtualPrinter,
  useVirtualPrinters,
} from "@/api/virtualPrinters";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

const MODELS: Array<[string, string]> = [
  ["N1", "A1 mini"],
  ["N2S", "A1"],
  ["C11", "P1P"],
  ["C12", "P1S"],
  ["N7", "P2S"],
  ["BL-P001", "X1 Carbon"],
  ["BL-P002", "X1"],
  ["C13", "X1E"],
  ["O1D", "H2D"],
  ["O1S", "H2S"],
  ["O1C", "H2C"],
];

function modes(t: TFunction): Array<[VirtualPrinterMode, string, string]> {
  return [
    ["queue", t("settingsVirtualPrinter.modeQueue"), t("settingsVirtualPrinter.hintQueue")],
    ["review", t("settingsVirtualPrinter.modeReview"), t("settingsVirtualPrinter.hintReview")],
    ["archive", t("settingsVirtualPrinter.modeArchive"), t("settingsVirtualPrinter.hintArchive")],
    ["proxy", t("settingsVirtualPrinter.modeProxy"), t("settingsVirtualPrinter.hintProxy")],
  ];
}

const SELECT_CLASS =
  "h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs disabled:opacity-50";

function modelLabel(code: string): string {
  return MODELS.find(([c]) => c === code)?.[1] ?? code;
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.detail : fallback;
}

interface FormProps {
  initial?: VirtualPrinter;
  onDone: () => void;
}

function VirtualPrinterForm({ initial, onDone }: FormProps) {
  const { t } = useTranslation();
  const create = useCreateVirtualPrinter();
  const update = useUpdateVirtualPrinter();
  const printers = usePrinters();
  const [name, setName] = useState(initial?.name ?? "Virtual A1 Mini");
  const [model, setModel] = useState(initial?.model ?? "N1");
  const [mode, setMode] = useState<VirtualPrinterMode>(initial?.mode ?? "queue");
  const [target, setTarget] = useState<string>(initial?.target_printer_id?.toString() ?? "");
  const pending = create.isPending || update.isPending;
  const error = create.error ?? update.error;

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const body = {
      name: name.trim(),
      model,
      mode,
      target_printer_id: mode === "proxy" && target ? Number(target) : null,
    };
    const opts = { onSuccess: onDone };
    if (initial) update.mutate({ id: initial.id, body }, opts);
    else create.mutate(body, opts);
  }

  return (
    <form onSubmit={submit} className="grid gap-3 rounded-md border p-3 sm:grid-cols-2">
      <div className="space-y-1">
        <Label htmlFor="vp-name">{t("settingsVirtualPrinter.name")}</Label>
        <Input id="vp-name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={64} />
      </div>
      <div className="space-y-1">
        <Label htmlFor="vp-model">{t("settingsVirtualPrinter.model")}</Label>
        <select id="vp-model" className={SELECT_CLASS} value={model} onChange={(e) => setModel(e.target.value)}>
          {MODELS.map(([code, label]) => (
            <option key={code} value={code}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div className="space-y-1">
        <Label htmlFor="vp-mode">{t("settingsVirtualPrinter.whenSent")}</Label>
        <select
          id="vp-mode"
          className={SELECT_CLASS}
          value={mode}
          onChange={(e) => setMode(e.target.value as VirtualPrinterMode)}
        >
          {modes(t).map(([value, label, hint]) => (
            <option key={value} value={value}>
              {label} — {hint}
            </option>
          ))}
        </select>
      </div>
      {mode === "proxy" && (
        <div className="space-y-1">
          <Label htmlFor="vp-target">{t("settingsVirtualPrinter.target")}</Label>
          <select
            id="vp-target"
            className={SELECT_CLASS}
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            required
          >
            <option value="">{t("settingsVirtualPrinter.choosePrinter")}</option>
            {(printers.data ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className="flex items-center gap-2 sm:col-span-2">
        <Button type="submit" disabled={pending || !name.trim() || (mode === "proxy" && !target)}>
          {initial ? t("common.save") : t("settingsVirtualPrinter.create")}
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          {t("common.cancel")}
        </Button>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {errorText(error, t("settingsVirtualPrinter.saveError"))}
          </p>
        )}
      </div>
    </form>
  );
}

function Row({ vp, onEdit }: { vp: VirtualPrinter; onEdit: () => void }) {
  const update = useUpdateVirtualPrinter();
  const del = useDeleteVirtualPrinter();
  const { t } = useTranslation();
  const [confirming, setConfirming] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const mode = modes(t).find(([m]) => m === vp.mode)?.[1] ?? vp.mode;
  const err = update.error ?? del.error;

  return (
    <li className="space-y-2 rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{vp.name}</span>
        <span className="text-xs text-muted-foreground">
          {modelLabel(vp.model)} · {mode}
          {!vp.enabled && ` · ${t("settingsVirtualPrinter.disabled")}`}
        </span>
        <div className="ml-auto flex gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={update.isPending}
            onClick={() => update.mutate({ id: vp.id, body: { enabled: !vp.enabled } })}
          >
            {vp.enabled ? t("settingsVirtualPrinter.disable") : t("settingsVirtualPrinter.enable")}
          </Button>
          <Button size="sm" variant="outline" onClick={onEdit}>
            {t("common.edit")}
          </Button>
          <Button size="sm" variant="outline" disabled={del.isPending} onClick={() => setConfirming(true)}>
            {t("common.delete")}
          </Button>
        </div>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-muted-foreground">{t("settingsVirtualPrinter.serial")}</dt>
        <dd className="font-mono">{vp.serial}</dd>
        <dt className="text-muted-foreground">{t("settingsVirtualPrinter.accessCode")}</dt>
        <dd className="flex items-center gap-2">
          <code className="font-mono" aria-label={t("settingsVirtualPrinter.accessCodeFor", { name: vp.name })}>
            {vp.access_code}
          </code>
          <Button
            size="sm"
            variant="ghost"
            disabled={update.isPending}
            onClick={() => setRegenerating(true)}
          >
            {t("settingsVirtualPrinter.regenerate")}
          </Button>
        </dd>
      </dl>
      {err && (
        <p role="alert" className="text-sm text-destructive">
          {errorText(err, t("settingsVirtualPrinter.requestFailed"))}
        </p>
      )}
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={t("virtualPrinter.deleteTitle", { name: vp.name })}
        description={t("virtualPrinter.deleteDesc")}
        confirmLabel={t("common.delete")}
        cancelLabel={t("common.cancel")}
        destructive
        onConfirm={() => del.mutate(vp.id)}
      />
      <ConfirmDialog
        open={regenerating}
        onOpenChange={setRegenerating}
        title={t("virtualPrinter.regenTitle")}
        description={t("virtualPrinter.regenDesc")}
        confirmLabel={t("virtualPrinter.regenerate")}
        cancelLabel={t("common.cancel")}
        destructive
        onConfirm={() => update.mutate({ id: vp.id, body: { regenerate_access_code: true } })}
      />
    </li>
  );
}

export function VirtualPrinterCard() {
  const { t } = useTranslation();
  const list = useVirtualPrinters();
  const [editing, setEditing] = useState<number | "new" | null>(null);

  if (list.isLoading) return <Skeleton className="h-40 w-full rounded-xl" />;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settingsVirtualPrinter.title")}</CardTitle>
        <CardDescription>
          {t("settingsVirtualPrinter.description")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {list.isError && (
          <p role="alert" className="text-sm text-destructive">
            {errorText(list.error, t("settingsVirtualPrinter.loadError"))}
          </p>
        )}
        {(list.data ?? []).length === 0 && editing !== "new" && (
          <p className="text-sm text-muted-foreground">{t("settingsVirtualPrinter.none")}</p>
        )}
        <ul className="space-y-3">
          {(list.data ?? []).map((vp) =>
            editing === vp.id ? (
              <li key={vp.id}>
                <VirtualPrinterForm initial={vp} onDone={() => setEditing(null)} />
              </li>
            ) : (
              <Row key={vp.id} vp={vp} onEdit={() => setEditing(vp.id)} />
            ),
          )}
        </ul>
        {editing === "new" ? (
          <VirtualPrinterForm onDone={() => setEditing(null)} />
        ) : (
          <Button variant="outline" onClick={() => setEditing("new")}>
            {t("settingsVirtualPrinter.add")}
          </Button>
        )}
        <div className="space-y-1 text-sm text-muted-foreground">
          <p className="font-medium text-foreground">{t("settingsVirtualPrinter.stepsTitle")}</p>
          <ol className="list-decimal space-y-0.5 pl-5">
            <li>
              <Trans i18nKey="settingsVirtualPrinter.step1" components={{ code: <code /> }} />
            </li>
            <li>{t("settingsVirtualPrinter.step2")}</li>
            <li>{t("settingsVirtualPrinter.step3")}</li>
          </ol>
        </div>
      </CardContent>
    </Card>
  );
}
