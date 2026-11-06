/**
 * Per-printer camera settings (Q4): external camera (kind + URL) and image
 * rotation stored in `printer.options` (`camera`, `camera_rotation`), plus
 * the OBS overlay token list. The API never echoes the camera URL (only
 * `{kind, has_url}`), so leaving the URL blank keeps the stored one.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import { useCreateOverlayToken, useOverlayTokens, useRevokeOverlayToken, useUpdatePrinter } from "@/api/printers";
import type { PrinterOut } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type CameraKind = "" | "mjpeg" | "rtsp" | "snapshot";

const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";

export function PrinterCameraSection({ printer }: { printer: PrinterOut }) {
  const { t } = useTranslation();
  const existing = printer.options.camera as { kind?: string; has_url?: boolean } | undefined;
  const [kind, setKind] = useState<CameraKind>((existing?.kind as CameraKind | undefined) ?? "");
  const [url, setUrl] = useState("");
  const [rotation, setRotation] = useState(Number(printer.options.camera_rotation ?? 0));
  const [layerTl, setLayerTl] = useState(Boolean(printer.options.layer_timelapse));
  const update = useUpdatePrinter(printer.id);
  const id = `printer-${printer.id}-camera`;

  function save() {
    const options: Record<string, unknown> = { ...printer.options, camera_rotation: rotation, layer_timelapse: layerTl };
    delete options.camera;
    if (kind) options.camera = url.trim() ? { kind, url: url.trim() } : { kind };
    update.mutate({ options }, { onSuccess: () => setUrl("") });
  }

  return (
    <fieldset className="flex flex-col gap-3 rounded-md border p-3">
      <legend className="px-1 text-sm font-medium">{t("settingsCamera.title")}</legend>
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-kind`}>{t("settingsCamera.external")}</Label>
          <select
            id={`${id}-kind`}
            className={selectClass}
            value={kind}
            onChange={(e) => setKind(e.target.value as CameraKind)}
          >
            <option value="">{t("settingsCamera.builtIn")}</option>
            <option value="mjpeg">{t("settingsCamera.mjpeg")}</option>
            <option value="rtsp">{t("settingsCamera.rtsp")}</option>
            <option value="snapshot">{t("settingsCamera.snapshot")}</option>
          </select>
        </div>
        <div className="flex flex-col gap-1.5 sm:col-span-2">
          <Label htmlFor={`${id}-url`}>{t("settingsCamera.url")}</Label>
          <Input
            id={`${id}-url`}
            value={url}
            disabled={!kind}
            placeholder={existing?.has_url ? t("settingsCamera.stored") : "http://..."}
            onChange={(e) => setUrl(e.target.value)}
            autoComplete="off"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-rotation`}>{t("settingsCamera.rotation")}</Label>
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
      <label className="flex items-center gap-2 text-sm" htmlFor={`${id}-layer-tl`}>
        <input
          id={`${id}-layer-tl`}
          type="checkbox"
          checked={layerTl}
          onChange={(e) => setLayerTl(e.target.checked)}
        />
        {t("settingsCamera.layerTimelapse")}
      </label>
      <div className="flex items-center gap-2">
        <Button type="button" size="sm" onClick={save} disabled={update.isPending}>
          {update.isPending ? t("settingsCamera.saving") : t("settingsCamera.save")}
        </Button>
        {update.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {update.error instanceof ApiError ? update.error.detail : t("settingsCamera.saveError")}
          </p>
        ) : null}
        {update.isSuccess ? <p className="text-sm text-success">{t("settingsCamera.saved")}</p> : null}
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
  const [revoking, setRevoking] = useState<number | null>(null);
  const { t: tr } = useTranslation();

  function mint() {
    create.mutate(undefined, {
      onSuccess: (t) => setMinted(`${window.location.origin}${t.overlay_url}`),
    });
  }

  const active = (tokens.data ?? []).filter((t) => t.revoked_at == null);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{tr("settingsCamera.overlayTokens")}</span>
        <Button type="button" size="sm" variant="outline" onClick={mint} disabled={create.isPending}>
          {tr("settingsCamera.createToken")}
        </Button>
      </div>
      {minted ? (
        <div className="flex flex-col gap-1 rounded-md bg-muted p-2 text-xs">
          <span>{tr("settingsCamera.copyOnce")}</span>
          <code className="break-all" data-testid="minted-overlay-url">
            {minted}
          </code>
          <Button type="button" size="sm" variant="ghost" className="self-start" onClick={() => setMinted(null)}>
            {tr("settingsCamera.done")}
          </Button>
        </div>
      ) : null}
      {active.length === 0 ? (
        <p className="text-xs text-muted-foreground">{tr("settingsCamera.noTokens")}</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {active.map((t) => (
            <li key={t.id} className="flex items-center justify-between text-sm">
              <span>
                {t.label ?? tr("settingsCamera.tokenFallback", { id: t.id })}{" "}
                <span className="text-xs text-muted-foreground">{new Date(t.created_at).toLocaleDateString()}</span>
              </span>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                aria-label={tr("settingsCamera.revokeAria", { id: t.id })}
                onClick={() => setRevoking(t.id)}
                disabled={revoke.isPending}
              >
                {tr("common.revoke")}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <ConfirmDialog
        open={revoking != null}
        onOpenChange={(open) => !open && setRevoking(null)}
        title={tr("overlayTokens.revokeTitle")}
        description={tr("overlayTokens.revokeDesc")}
        confirmLabel={tr("tokens.revoke")}
        cancelLabel={tr("common.cancel")}
        destructive
        onConfirm={() => revoking != null && revoke.mutate(revoking)}
      />
    </div>
  );
}
