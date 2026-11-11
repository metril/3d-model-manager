import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { DockviewReact, type DockviewApi, type DockviewReadyEvent, type IDockviewHeaderActionsProps } from "dockview-react";
import { ExternalLinkIcon, Undo2Icon } from "lucide-react";
import { toast } from "sonner";

import { syncThemeInto } from "@/components/model-detail/dock/popoutTheme";
import { LockableTab } from "@/components/dock/lockGating";
import { tabMenuItems, useDockLock } from "@/components/dock/lockGatingHooks";
import { LayoutEditBanner } from "@/components/dock/LayoutEditBanner";
import { useLayoutLocked } from "@/lib/layoutLock";
import { RelocateContext, createRelocateEmitter } from "@/lib/windowRelocate";
import { addPanelAtDefault, buildDefaultLayout } from "./defaultLayout";
import { flushLayout, loadLayout, resetLayout, saveLayout } from "./layoutStore";
import { DOCK_COMPONENTS } from "./panels";
import { PANEL_TITLE_KEYS } from "./panelMeta";
import i18n from "i18next";

/** Dockview captures titles at add time; re-apply them from the current language. */
function retitlePanels(api: DockviewApi) {
  for (const p of api.panels) {
    const key = (PANEL_TITLE_KEYS as Record<string, string>)[p.id];
    if (key) p.api.setTitle(i18n.t(key));
  }
}

import { useTranslation } from "react-i18next";

const POPOUT_URL = "/popout.html";
const DOCK_THEME = { name: "tdmm", className: "dockview-theme-tdmm", gap: 6 };

/** Group header button: pop the group into its own window, or (inside a
 * pop-out) close the window, which docks the panels back. */
function PopoutAction({ group, api, containerApi }: IDockviewHeaderActionsProps) {
  const { t } = useTranslation();
  const locked = useLayoutLocked();
  const inPopout = api.location.type === "popout";
  if (locked && !inPopout) return null;
  return (
    <button
      type="button"
      className="mx-1 inline-flex size-6 items-center justify-center self-center rounded text-muted-foreground hover:bg-accent hover:text-accent-foreground"
      aria-label={inPopout ? t("printDock.returnToDock") : t("printDock.popOut")}
      title={inPopout ? t("printDock.returnToDock") : t("printDock.popOutWindow")}
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
  const { t } = useTranslation();
  const emitter = useMemo(() => createRelocateEmitter(), []);
  const [dockApi, setDockApi] = useState<DockviewApi | null>(null);
  const locked = useDockLock(dockApi);
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

  const langOff = useRef<(() => void) | null>(null);
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
      retitlePanels(api);
      langOff.current?.();
      const onLang = () => retitlePanels(api);
      i18n.on("languageChanged", onLang);
      langOff.current = () => i18n.off("languageChanged", onLang);
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
      api.onDidOpenPopoutWindowFail(() => toast.error(t("printDock.popupBlocked")));
      setDockApi(api);
      onApi?.(api);
    },
    [emitter, onApi, t],
  );

  const unmounted = useRef(false);
  useEffect(() => {
    const syncs = themeSyncs.current;
    unmounted.current = false;
    return () => {
      langOff.current?.();
      langOff.current = null;
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
      <LayoutEditBanner
        onReset={() => {
          if (!dockApi) return;
          resetLayout();
          buildDefaultLayout(dockApi);
        }}
      />
      <div ref={wrapRef} className={locked ? "w-full tdmm-dock-locked" : "w-full"} style={{ height: height ?? "calc(100svh - 10rem)" }} data-testid="print-dock">
        <DockviewReact
          components={DOCK_COMPONENTS}
          rightHeaderActionsComponent={PopoutAction}
          defaultTabComponent={LockableTab}
          getTabContextMenuItems={() => tabMenuItems(locked)}
          popoutUrl={POPOUT_URL}
          theme={DOCK_THEME}
          onReady={handleReady}
        />
      </div>
    </RelocateContext.Provider>
  );
}
