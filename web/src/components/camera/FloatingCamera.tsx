/**
 * Floating, draggable, resizable camera overlay mounted once in AppShell so
 * it persists across route changes. State lives in `lib/floatingCamera`.
 * Escape closes it (unless focus is elsewhere); "Pop out" uses Document
 * Picture-in-Picture when available, otherwise the button is hidden and the
 * in-app overlay is the only mode.
 */
import { MinusIcon, PictureInPicture2Icon, SquareIcon, XIcon } from "lucide-react";
import { useEffect, useRef, type PointerEvent as ReactPointerEvent } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

import { usePrintersOverview } from "@/api/printers";
import { CameraTile } from "@/components/print/CameraTile";
import { useHotkeys } from "@/hooks/useHotkeys";
import { floatingCamera, MIN_H, MIN_W, useFloatingCamera, type FloatingRect } from "@/lib/floatingCamera";
import { pipSupported, popOutElement } from "@/lib/pip";

/** Keeps the overlay fully reachable: its top-left corner (and so the drag
 * handle) stays on-screen and it never extends past the viewport. */
function clampToViewport(r: FloatingRect): FloatingRect {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const w = Math.min(r.w, Math.max(MIN_W, vw));
  const h = Math.min(r.h, Math.max(MIN_H, vh));
  return {
    ...r,
    w,
    h,
    x: Math.min(Math.max(0, r.x), Math.max(0, vw - w)),
    y: Math.min(Math.max(0, r.y), Math.max(0, vh - h)),
  };
}

export function FloatingCamera() {
  const { t } = useTranslation();
  const s = useFloatingCamera();
  const overview = usePrintersOverview();
  const rootRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const dragCleanup = useRef<(() => void) | null>(null);
  // Drop any in-progress drag listeners if the overlay unmounts mid-drag.
  useEffect(() => () => dragCleanup.current?.(), []);
  const id = s.printerId;
  const firstId = overview.data?.printers?.[0]?.printer.id;

  // Shift+C toggles the overlay (first printer when none is remembered).
  useHotkeys({
    "shift+C": () => {
      if (floatingCamera.get().printerId != null) floatingCamera.close();
      else if (firstId != null) floatingCamera.open(firstId);
    },
  });

  useEffect(() => {
    if (id == null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      if (document.fullscreenElement) return;
      const active = document.activeElement;
      if (active && active !== document.body && !rootRef.current?.contains(active)) return;
      e.preventDefault();
      floatingCamera.close();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [id]);

  if (id == null) return null;
  const item = overview.data?.printers?.find((p) => p.printer.id === id);
  const name = item?.printer.name ?? t("cameraUi.printerFallback", { id });
  const online = item?.status?.online ?? false;
  const { rect } = s;

  function drag(e: ReactPointerEvent, mode: "move" | "resize") {
    if (e.button !== 0) return;
    e.preventDefault();
    dragCleanup.current?.();
    const start = { x: e.clientX, y: e.clientY, rect: { ...floatingCamera.get().rect } };
    const move = (ev: PointerEvent) => {
      const dx = ev.clientX - start.x;
      const dy = ev.clientY - start.y;
      const next: FloatingRect =
        mode === "move"
          ? { ...start.rect, x: start.rect.x + dx, y: start.rect.y + dy }
          : { ...start.rect, w: Math.max(MIN_W, start.rect.w + dx), h: Math.max(MIN_H, start.rect.h + dy) };
      floatingCamera.setRect(clampToViewport(next));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      dragCleanup.current = null;
    };
    dragCleanup.current = up;
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  }

  async function popOut() {
    const el = bodyRef.current;
    if (el) await popOutElement(el, name, rect.w, rect.h);
  }

  return createPortal(
    <div
      ref={rootRef}
      role="complementary"
      aria-label={t("cameraUi.overlay", { name })}
      tabIndex={-1}
      data-testid="floating-camera"
      className="fixed z-50 flex flex-col overflow-hidden rounded-lg border bg-black text-white shadow-2xl"
      style={
        s.minimized
          ? { left: rect.x, top: rect.y, width: Math.min(rect.w, 260) }
          : { left: rect.x, top: rect.y, width: rect.w, height: rect.h }
      }
    >
      <div
        className="flex cursor-move touch-none items-center gap-1 bg-black/80 px-2 py-1 text-xs"
        onPointerDown={(e) => drag(e, "move")}
      >
        <span className="min-w-0 flex-1 truncate font-medium">{name}</span>
        <div className="flex items-center gap-0.5" onPointerDown={(e) => e.stopPropagation()}>
          {pipSupported() && !s.minimized ? (
            <HeaderButton label={t("cameraUi.popOut")} onClick={() => void popOut()}>
              <PictureInPicture2Icon className="size-3.5" />
            </HeaderButton>
          ) : null}
          <HeaderButton
            label={s.minimized ? t("cameraUi.restore") : t("cameraUi.minimise")}
            onClick={() => floatingCamera.setMinimized(!s.minimized)}
          >
            {s.minimized ? <SquareIcon className="size-3.5" /> : <MinusIcon className="size-3.5" />}
          </HeaderButton>
          <HeaderButton label={t("cameraUi.close")} onClick={() => floatingCamera.close()}>
            <XIcon className="size-3.5" />
          </HeaderButton>
        </div>
      </div>
      {s.minimized ? null : (
        <>
          <div ref={bodyRef} className="min-h-0 flex-1">
            <CameraTile printerId={id} online={online} className="bg-black" overlay />
          </div>
          <div
            role="separator"
            aria-label={t("cameraUi.resize")}
            className="absolute bottom-0 right-0 size-4 cursor-nwse-resize touch-none"
            onPointerDown={(e) => drag(e, "resize")}
          />
        </>
      )}
    </div>,
    document.body,
  );
}

function HeaderButton({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="grid size-5 place-items-center rounded hover:bg-white/20 focus-visible:outline focus-visible:outline-2"
    >
      {children}
    </button>
  );
}
