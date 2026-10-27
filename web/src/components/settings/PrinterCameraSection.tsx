/**
 * Per-printer camera settings (Q4): external camera (kind + URL) and image
 * rotation stored in `printer.options` (`camera`, `camera_rotation`), plus
 * the OBS overlay token list. The API never echoes the camera URL (only
 * `{kind, has_url}`), so leaving the URL blank keeps the stored one.
 */
import { useState } from "react";

import { ApiError } from "@/api/client";
import { useCreateOverlayToken, useOverlayTokens, useRevokeOverlayToken, useUpdatePrinter } from "@/api/printers";
import type { PrinterOut } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type CameraKind = "" | "mjpeg" | "rtsp" | "snapshot";

const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";

export function PrinterCameraSection({ printer }: { printer: PrinterOut }) {
  const existing = printer.options.camera as { kind?: string; has_url?: boolean } | undefined;
  const [kind, setKind] = useState<CameraKind>((existing?.kind as CameraKind | undefined) ?? "");
  const [url, setUrl] = useState("");
  const [rotation, setRotation] = useState(Number(printer.options.camera_rotation ?? 0));
  const update = useUpdatePrinter(printer.id);
  const id = `printer-${printer.id}-camera`;

  function save() {
    const options: Record<string, unknown> = { ...printer.options, camera_rotation: rotation };
    delete options.camera;
    if (kind) options.camera = url.trim() ? { kind, url: url.trim() } : { kind };
    update.mutate({ options }, { onSuccess: () => setUrl("") });
  }

  return (
    <fieldset className="flex flex-col gap-3 rounded-md border p-3">
      <legend className="px-1 text-sm font-medium">Camera</legend>
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-kind`}>External camera</Label>
          <select
            id={`${id}-kind`}
            className={selectClass}
            value={kind}
            onChange={(e) => setKind(e.target.value as CameraKind)}
          >
            <option value="">Built-in (printer)</option>
            <option value="mjpeg">MJPEG URL</option>
            <option value="rtsp">RTSP URL</option>
            <option value="snapshot">Snapshot URL</option>
          </select>
        </div>
        <div className="flex flex-col gap-1.5 sm:col-span-2">
          <Label htmlFor={`${id}-url`}>URL</Label>
          <Input
            id={`${id}-url`}
            value={url}
            disabled={!kind}
            placeholder={existing?.has_url ? "Stored (leave blank to keep)" : "http://..."}
            onChange={(e) => setUrl(e.target.value)}
            autoComplete="off"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-rotation`}>Rotation</Label>
          <select
            id={`${id}-rotation`}
            className={selectClass}
            value={rotation}
            onChange={(e) => setRotation(Number(e.target.value))}
          >
            {[0, 90, 180, 270].map((r) => (
              <option key={r} value={r}>
                {r}°
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <Button type="button" size="sm" onClick={save} disabled={update.isPending}>
          {update.isPending ? "Saving..." : "Save camera"}
        </Button>
        {update.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {update.error instanceof ApiError ? update.error.detail : "Could not save camera"}
          </p>
        ) : null}
        {update.isSuccess ? <p className="text-sm text-success">Saved.</p> : null}
      </div>
      <OverlayTokensList printerId={printer.id} />
    </fieldset>
  );
}

export function OverlayTokensList({ printerId }: { printerId: number }) {
  const tokens = useOverlayTokens(printerId);
  const create = useCreateOverlayToken(printerId);
  const revoke = useRevokeOverlayToken(printerId);
  const [minted, setMinted] = useState<string | null>(null);

  function mint() {
    create.mutate(undefined, {
      onSuccess: (t) => setMinted(`${window.location.origin}${t.overlay_url}`),
    });
  }

  const active = (tokens.data ?? []).filter((t) => t.revoked_at == null);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">Overlay tokens</span>
        <Button type="button" size="sm" variant="outline" onClick={mint} disabled={create.isPending}>
          Create overlay token
        </Button>
      </div>
      {minted ? (
        <div className="flex flex-col gap-1 rounded-md bg-muted p-2 text-xs">
          <span>Copy this OBS browser-source URL now; it is shown only once.</span>
          <code className="break-all" data-testid="minted-overlay-url">
            {minted}
          </code>
          <Button type="button" size="sm" variant="ghost" className="self-start" onClick={() => setMinted(null)}>
            Done
          </Button>
        </div>
      ) : null}
      {active.length === 0 ? (
        <p className="text-xs text-muted-foreground">No active overlay tokens.</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {active.map((t) => (
            <li key={t.id} className="flex items-center justify-between text-sm">
              <span>
                {t.label ?? `Token #${t.id}`}{" "}
                <span className="text-xs text-muted-foreground">{new Date(t.created_at).toLocaleDateString()}</span>
              </span>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                aria-label={`Revoke token ${t.id}`}
                onClick={() => revoke.mutate(t.id)}
                disabled={revoke.isPending}
              >
                Revoke
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
