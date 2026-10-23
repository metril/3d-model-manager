/**
 * Virtual printer (Q11): a fake LAN Bambu printer slicers can "print" to.
 * Lists/creates/edits/deletes `virtual_printers` rows and shows each one's
 * serial + access code with Bambu Studio setup steps. Rendered on the
 * Settings -> Printer tab only when `GET /features` reports
 * `virtual_printer_enabled` (the `virtual-printer` compose service is opt-in).
 */
import { useState } from "react";

import { ApiError } from "@/api/client";
import { usePrinters } from "@/api/printers";
import type { VirtualPrinter, VirtualPrinterMode } from "@/api/types";
import {
  useCreateVirtualPrinter,
  useDeleteVirtualPrinter,
  useUpdateVirtualPrinter,
  useVirtualPrinters,
} from "@/api/virtualPrinters";
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

const MODES: Array<[VirtualPrinterMode, string, string]> = [
  ["queue", "Queue", "add to the print queue"],
  ["review", "Review", "add to the queue, pending approval in the review inbox"],
  ["archive", "Archive", "store the file in the library only"],
  ["proxy", "Proxy", "queue it for a real printer (the dispatcher maps the AMS)"],
];

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
        <Label htmlFor="vp-name">Name</Label>
        <Input id="vp-name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={64} />
      </div>
      <div className="space-y-1">
        <Label htmlFor="vp-model">Model</Label>
        <select id="vp-model" className={SELECT_CLASS} value={model} onChange={(e) => setModel(e.target.value)}>
          {MODELS.map(([code, label]) => (
            <option key={code} value={code}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div className="space-y-1">
        <Label htmlFor="vp-mode">When a slicer sends a print</Label>
        <select
          id="vp-mode"
          className={SELECT_CLASS}
          value={mode}
          onChange={(e) => setMode(e.target.value as VirtualPrinterMode)}
        >
          {MODES.map(([value, label, hint]) => (
            <option key={value} value={value}>
              {label} — {hint}
            </option>
          ))}
        </select>
      </div>
      {mode === "proxy" && (
        <div className="space-y-1">
          <Label htmlFor="vp-target">Target printer</Label>
          <select
            id="vp-target"
            className={SELECT_CLASS}
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            required
          >
            <option value="">Choose a printer…</option>
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
          {initial ? "Save" : "Create virtual printer"}
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {errorText(error, "Could not save the virtual printer.")}
          </p>
        )}
      </div>
    </form>
  );
}

function Row({ vp, onEdit }: { vp: VirtualPrinter; onEdit: () => void }) {
  const update = useUpdateVirtualPrinter();
  const del = useDeleteVirtualPrinter();
  const [confirming, setConfirming] = useState(false);
  const mode = MODES.find(([m]) => m === vp.mode)?.[1] ?? vp.mode;
  const err = update.error ?? del.error;

  return (
    <li className="space-y-2 rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{vp.name}</span>
        <span className="text-xs text-muted-foreground">
          {modelLabel(vp.model)} · {mode}
          {!vp.enabled && " · disabled"}
        </span>
        <div className="ml-auto flex gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={update.isPending}
            onClick={() => update.mutate({ id: vp.id, body: { enabled: !vp.enabled } })}
          >
            {vp.enabled ? "Disable" : "Enable"}
          </Button>
          <Button size="sm" variant="outline" onClick={onEdit}>
            Edit
          </Button>
          {confirming ? (
            <Button size="sm" variant="destructive" disabled={del.isPending} onClick={() => del.mutate(vp.id)}>
              Confirm delete
            </Button>
          ) : (
            <Button size="sm" variant="outline" onClick={() => setConfirming(true)}>
              Delete
            </Button>
          )}
        </div>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-muted-foreground">Serial</dt>
        <dd className="font-mono">{vp.serial}</dd>
        <dt className="text-muted-foreground">Access code</dt>
        <dd className="flex items-center gap-2">
          <code className="font-mono" aria-label={`Access code for ${vp.name}`}>
            {vp.access_code}
          </code>
          <Button
            size="sm"
            variant="ghost"
            disabled={update.isPending}
            onClick={() => update.mutate({ id: vp.id, body: { regenerate_access_code: true } })}
          >
            Regenerate
          </Button>
        </dd>
      </dl>
      {err && (
        <p role="alert" className="text-sm text-destructive">
          {errorText(err, "Request failed.")}
        </p>
      )}
    </li>
  );
}

export function VirtualPrinterCard() {
  const list = useVirtualPrinters();
  const [editing, setEditing] = useState<number | "new" | null>(null);

  if (list.isLoading) return <Skeleton className="h-40 w-full rounded-xl" />;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Virtual printer</CardTitle>
        <CardDescription>
          A fake LAN printer your slicer can send to. The file lands in the queue, the review inbox or the
          library, or is proxied to a real printer. Only the first enabled virtual printer is served.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {list.isError && (
          <p role="alert" className="text-sm text-destructive">
            {errorText(list.error, "Couldn't load virtual printers.")}
          </p>
        )}
        {(list.data ?? []).length === 0 && editing !== "new" && (
          <p className="text-sm text-muted-foreground">No virtual printers yet.</p>
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
            Add virtual printer
          </Button>
        )}
        <div className="space-y-1 text-sm text-muted-foreground">
          <p className="font-medium text-foreground">In Bambu Studio / OrcaSlicer</p>
          <ol className="list-decimal space-y-0.5 pl-5">
            <li>Start the service: <code>docker compose --profile virtual-printer up -d</code>.</li>
            <li>Open Device, pick the printer by LAN and choose the virtual printer by name (or add it with the host IP + serial).</li>
            <li>Enter the access code above when asked, then use Print / Send as usual.</li>
          </ol>
        </div>
      </CardContent>
    </Card>
  );
}
