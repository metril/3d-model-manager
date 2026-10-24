import { useCallback, useEffect, useMemo, useRef } from "react";
import { DockviewReact, type DockviewApi, type DockviewReadyEvent, type IDockviewHeaderActionsProps } from "dockview-react";
import { ExternalLinkIcon, Undo2Icon } from "lucide-react";
import { toast } from "sonner";

import { RelocateContext, createRelocateEmitter } from "@/lib/windowRelocate";
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
  const emitter = useMemo(() => createRelocateEmitter(), []);
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
      <div className="h-[calc(100svh-13rem)] min-h-[640px] w-full" data-testid="model-detail-dock">
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
