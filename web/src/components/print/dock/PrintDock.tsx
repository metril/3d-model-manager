import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { DockviewReact, type DockviewApi, type DockviewReadyEvent, type IDockviewHeaderActionsProps } from "dockview-react";
import { ExternalLinkIcon, Undo2Icon } from "lucide-react";
import { toast } from "sonner";

import { syncThemeInto } from "@/components/model-detail/dock/popoutTheme";
import { RelocateContext, createRelocateEmitter } from "@/lib/windowRelocate";
import { addPanelAtDefault, buildDefaultLayout } from "./defaultLayout";
import { flushLayout, loadLayout, saveLayout } from "./layoutStore";
import { DOCK_COMPONENTS } from "./panels";

const POPOUT_URL = "/popout.html";
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

/** The print dashboard as a dockable workspace (same mechanics as the model
 * detail dock). The layout persists per browser; pop-outs never survive reload. */
export function PrintDock({ onApi }: { onApi?: (api: DockviewApi | null) => void }) {
  const emitter = useMemo(() => createRelocateEmitter(), []);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<number | undefined>(undefined);
  useLayoutEffect(() => {
    const measure = () => {
      const el = wrapRef.current;
      if (!el) return;
      const top = el.getBoundingClientRect().top + window.scrollY;
      setHeight(Math.max(480, Math.floor(window.innerHeight - top - 24)));
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
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
      if (!restored) buildDefaultLayout(api);

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
    [emitter, onApi],
  );

  const unmounted = useRef(false);
  useEffect(() => {
    const syncs = themeSyncs.current;
    unmounted.current = false;
    return () => {
      flushLayout();
      syncs.forEach((dispose) => dispose());
      syncs.clear();
      unmounted.current = true;
      queueMicrotask(() => {
        if (unmounted.current) onApi?.(null);
      });
    };
  }, [onApi]);

  return (
    <RelocateContext.Provider value={emitter}>
      <div ref={wrapRef} className="w-full" style={{ height: height ?? "calc(100svh - 10rem)" }} data-testid="print-dock">
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
