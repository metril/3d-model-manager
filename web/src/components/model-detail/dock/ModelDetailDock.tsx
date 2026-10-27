import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { DockviewReact, type DockviewApi, type DockviewReadyEvent, type IDockviewHeaderActionsProps } from "dockview-react";
import { ExternalLinkIcon, Undo2Icon } from "lucide-react";
import { toast } from "sonner";

import { RelocateContext, createRelocateEmitter } from "@/lib/windowRelocate";
import { useModelDetail } from "./ModelDetailContext";
import { buildDefaultLayout, addPanelAtDefault } from "./defaultLayout";
import { flushLayout, loadLayout, saveLayout } from "./layoutStore";
import { DOCK_COMPONENTS } from "./panels";
import { syncThemeInto } from "./popoutTheme";

const POPOUT_URL = "/popout.html";
// dockview applies its (dark) default theme to the dock element unless told otherwise.
const DOCK_THEME = { name: "tdmm", className: "dockview-theme-tdmm", gap: 6 };

/** Group header button: pop the group into its own window, or (inside a
 * pop-out) close the window, which docks the panels back. */
function PopoutAction({ group, api, containerApi }: IDockviewHeaderActionsProps) {
  const inPopout = api.location.type === "popout";
  return (
    <button
      type="button"
      className="mx-1 inline-flex size-6 items-center justify-center self-center rounded text-muted-foreground hover:bg-accent hover:text-accent-foreground"
      aria-label={inPopout ? "Return to dock" : "Pop out"}
      title={inPopout ? "Return to dock" : "Pop out to a window"}
      onClick={() => {
        if (inPopout) api.getWindow().close();
        else void containerApi.addPopoutGroup(group, { popoutUrl: POPOUT_URL });
      }}
    >
      {inPopout ? <Undo2Icon className="size-3.5" /> : <ExternalLinkIcon className="size-3.5" />}
    </button>
  );
}

/** The model detail page as a dockable workspace: tabs can be dragged between
 * groups, resized, closed/re-added, and popped out into live windows. The
 * layout persists per browser (`layoutStore`); pop-outs never survive reload. */
export function ModelDetailDock({ onApi }: { onApi?: (api: DockviewApi | null) => void }) {
  const { model } = useModelDetail();
  const hasSliced = (model.current_revision?.files ?? []).some((file) => file.kind === "sliced");
  const emitter = useMemo(() => createRelocateEmitter(), []);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<number | undefined>(undefined);
  // Fit the viewport: everything below the dock's top edge (minus the shell's
  // bottom padding) so the page itself never scrolls; sane floor for tiny windows.
  useLayoutEffect(() => {
    const measure = () => {
      const el = wrapRef.current;
      if (!el) return;
      const top = el.getBoundingClientRect().top + window.scrollY;
      setHeight(Math.max(420, Math.floor(window.innerHeight - top - 24)));
    };
    measure();
    window.addEventListener("resize", measure);
    // Banners/headers above the dock can appear or wrap after mount.
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    // Observe everything laid out above the dock (its ancestors' earlier
    // siblings): their size is what moves the dock's top edge.
    for (let el: Element | null = wrapRef.current; el && el !== document.body; el = el.parentElement) {
      ro?.observe(el);
      for (let sib = el.previousElementSibling; sib; sib = sib.previousElementSibling) ro?.observe(sib);
    }
    return () => {
      window.removeEventListener("resize", measure);
      ro?.disconnect();
    };
  }, []);
  const themeSyncs = useRef(new Map<string, () => void>());

  const handleReady = useCallback(
    ({ api }: DockviewReadyEvent) => {
      const saved = loadLayout();
      let restored = false;
      if (saved) {
        try {
          api.fromJSON(saved.layout);
          restored = true;
          for (const id of saved.unseen) addPanelAtDefault(api, id);
        } catch {
          restored = false;
        }
      }
      if (!restored) buildDefaultLayout(api, { hasSliced });

      // Disposed with the dockview api when the component unmounts.
      api.onDidLayoutChange(() => saveLayout(api.toJSON()));
      api.onDidAddPopoutGroup((popout) => {
        themeSyncs.current.get(popout.id)?.();
        themeSyncs.current.set(popout.id, syncThemeInto(popout.window.document.documentElement));
        emitter.emit();
      });
      api.onDidRemovePopoutGroup((popout) => {
        themeSyncs.current.get(popout.id)?.();
        themeSyncs.current.delete(popout.id);
        emitter.emit();
      });
      api.onDidOpenPopoutWindowFail(() => toast.error("Allow pop-ups to open panels in a window"));
      onApi?.(api);
    },
    [emitter, onApi, hasSliced],
  );

  const unmounted = useRef(false);
  useEffect(() => {
    const syncs = themeSyncs.current;
    unmounted.current = false;
    return () => {
      flushLayout();
      syncs.forEach((dispose) => dispose());
      syncs.clear();
      // Only a real unmount reports "no api": StrictMode's simulated unmount
      // re-runs this effect synchronously, which clears the flag again.
      unmounted.current = true;
      queueMicrotask(() => {
        if (unmounted.current) onApi?.(null);
      });
    };
  }, [onApi]);

  return (
    <RelocateContext.Provider value={emitter}>
      <div
        ref={wrapRef}
        className="w-full"
        style={{ height: height ?? "calc(100svh - 10rem)" }}
        data-testid="model-detail-dock">
        <DockviewReact
          components={DOCK_COMPONENTS}
          rightHeaderActionsComponent={PopoutAction}
          getTabContextMenuItems={() => ["popout", "maximize", "separator", "close"]}
          popoutUrl={POPOUT_URL}
          theme={DOCK_THEME}
          onReady={handleReady}
        />
      </div>
    </RelocateContext.Provider>
  );
}
