/**
 * `/scan` (C12): scan label QR codes with the camera. Scan a location label to
 * make it the active location, then scan spool labels to put each spool there;
 * scanning a spool with no active location opens it.
 */
import { getRouteApi, useNavigate } from "@tanstack/react-router";
import jsQR from "jsqr";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useLocations } from "@/api/spoolAdmin";
import { usePatchSpool } from "@/api/spools";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PageContainer } from "@/components/ui/page-container";
import { parseScanUrl, type ScanTarget } from "@/lib/scanUrl";

const routeApi = getRouteApi("/authenticated/scan");

interface Detector {
  detect(source: CanvasImageSource): Promise<{ rawValue: string }[]>;
}

function makeDetector(): Detector | null {
  const Ctor = (globalThis as { BarcodeDetector?: new (o: { formats: string[] }) => Detector }).BarcodeDetector;
  return Ctor ? new Ctor({ formats: ["qr_code"] }) : null;
}

export function ScanPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { location: locParam } = routeApi.useSearch();
  const locations = useLocations();
  const patch = usePatchSpool();
  const activeId = locParam ? Number(locParam) : null;
  const active = (locations.data ?? []).find((l) => l.id === activeId) ?? null;
  const [manual, setManual] = useState("");
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [assigned, setAssigned] = useState<number[]>([]);
  const videoRef = useRef<HTMLVideoElement>(null);
  const lastRef = useRef<{ text: string; at: number }>({ text: "", at: 0 });

  const handle = useCallback(
    (target: ScanTarget) => {
      if (target.kind === "location") {
        setAssigned([]);
        void navigate({ to: "/scan", search: { location: String(target.id) }, replace: true });
        return;
      }
      if (activeId == null) {
        void navigate({ to: "/inventory/spools", search: { spool: String(target.id) } });
        return;
      }
      patch.mutate(
        { id: target.id, payload: { location_id: activeId } },
        {
          onSuccess: () => {
            setAssigned((prev) => (prev.includes(target.id) ? prev : [...prev, target.id]));
            toast.success(t("scan.assigned", { id: target.id }));
          },
          onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("scan.assignFailed", { id: target.id })),
        },
      );
    },
    [activeId, navigate, patch, t],
  );
  const handleRef = useRef(handle);
  useEffect(() => {
    handleRef.current = handle;
  }, [handle]);

  useEffect(() => {
    let stream: MediaStream | null = null;
    let timer = 0;
    let stopped = false;
    const detector = makeDetector();
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    async function tick() {
      const v = videoRef.current;
      if (stopped || !v) return;
      if (v.readyState >= 2 && v.videoWidth > 0) {
        let text: string | null = null;
        try {
          if (detector) {
            text = (await detector.detect(v))[0]?.rawValue ?? null;
          } else if (ctx) {
            canvas.width = v.videoWidth;
            canvas.height = v.videoHeight;
            ctx.drawImage(v, 0, 0);
            const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
            text = jsQR(img.data, img.width, img.height)?.data ?? null;
          }
        } catch {
          text = null;
        }
        const now = Date.now();
        const last = lastRef.current;
        if (text && (text !== last.text || now - last.at > 3000)) {
          lastRef.current = { text, at: now };
          const target = parseScanUrl(text);
          if (target) handleRef.current(target);
        }
      }
      if (!stopped) timer = window.setTimeout(() => void tick(), 250);
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setCameraError(t("scan.noCamera"));
      return;
    }
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: "environment" } })
      .then((s) => {
        if (stopped) {
          s.getTracks().forEach((tr) => tr.stop());
          return;
        }
        stream = s;
        if (videoRef.current) {
          videoRef.current.srcObject = s;
          void videoRef.current.play().catch(() => undefined);
        }
        void tick();
      })
      .catch(() => setCameraError(t("scan.cameraDenied")));
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      stream?.getTracks().forEach((tr) => tr.stop());
    };
  }, [t]);

  function submitManual(e: FormEvent) {
    e.preventDefault();
    const bare = /^#?\d+$/.test(manual.trim()) ? { kind: "spool" as const, id: Number(manual.trim().replace("#", "")) } : null;
    const target = parseScanUrl(manual) ?? bare;
    if (!target) {
      toast.error(t("scan.unrecognised"));
      return;
    }
    setManual("");
    handle(target);
  }

  return (
    <PageContainer width="narrow">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("scan.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("scan.desc")}</p>
      </div>
      <div className="rounded-lg border bg-muted/30 p-3 text-sm" role="status">
        {active ? (
          <>
            <span className="font-medium">{t("scan.active", { name: active.name })}</span>
            {assigned.length > 0 ? (
              <span className="ml-2 text-muted-foreground">{t("scan.count", { count: assigned.length })}</span>
            ) : null}
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="ml-2"
              onClick={() => void navigate({ to: "/scan", search: {}, replace: true })}
            >
              {t("scan.clear")}
            </Button>
          </>
        ) : (
          <span className="text-muted-foreground">{t("scan.noLocation")}</span>
        )}
      </div>
      {cameraError ? (
        <p role="alert" className="text-sm text-destructive">
          {cameraError}
        </p>
      ) : (
        <video ref={videoRef} muted playsInline className="aspect-video w-full rounded-lg bg-black object-cover" />
      )}
      <form onSubmit={submitManual} className="flex gap-2">
        <Input aria-label={t("scan.manual")} placeholder={t("scan.manual")} value={manual} onChange={(e) => setManual(e.target.value)} />
        <Button type="submit" disabled={!manual.trim()}>
          {t("scan.go")}
        </Button>
      </form>
    </PageContainer>
  );
}
