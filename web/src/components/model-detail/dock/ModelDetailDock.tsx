import { useTranslation } from "react-i18next";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { DockviewReact, type DockviewApi, type DockviewReadyEvent, type IDockviewHeaderActionsProps } from "dockview-react";
import { ExternalLinkIcon, Undo2Icon } from "lucide-react";
import { toast } from "sonner";

import { LockableTab } from "@/components/dock/lockGating";
import { tabMenuItems, useDockLock } from "@/components/dock/lockGatingHooks";
import { LayoutEditBanner } from "@/components/dock/LayoutEditBanner";
import { useLayoutLocked } from "@/lib/layoutLock";
import { RelocateContext, createRelocateEmitter } from "@/lib/windowRelocate";
import { useModelDetail } from "./ModelDetailContext";
import { buildDefaultLayout, addPanelAtDefault, hasProfileContent } from "./defaultLayout";
import { flushLayout, loadLayout, resetLayout, saveLayout } from "./layoutStore";
import { DOCK_COMPONENTS, PANEL_TITLE_KEYS } from "./panels";
import i18n from "i18next";

/** Dockview captures titles at add time; re-apply them from the current language. */
function retitlePanels(api: DockviewApi) {
  for (const p of api.panels) {
    const key = (PANEL_TITLE_KEYS as Record<string, string>)[p.id];
    if (key) p.api.setTitle(i18n.t(key));
  }
}

import { syncThemeInto } from "./popoutTheme";

const POPOUT_URL = "/popout.html";
// dockview applies its (dark) default theme to the dock element unless told otherwise.
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
      aria-label={inPopout ? t("mdDock.returnToDock") : t("mdDock.popOut")}
      title={inPopout ? t("mdDock.returnToDock") : t("mdDock.popOutWindow")}
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
  const { t } = useTranslation();
  const { model } = useModelDetail();
  // Unsliced print profiles (plated 3MFs) live in the same panel, so they count too.
  const hasSliced = hasProfileContent(model.current_revision?.files ?? []);
  const emitter = useMemo(() => createRelocateEmitter(), []);
  const [dockApi, setDockApi] = useState<DockviewApi | null>(null);
  const locked = useDockLock(dockApi);
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
      api.onDidOpenPopoutWindowFail(() => toast.error(t("mdDock.allowPopups")));
      setDockApi(api);
      onApi?.(api);
    },
    [emitter, onApi, hasSliced, t],
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
      <LayoutEditBanner
        onReset={() => {
          if (!dockApi) return;
          resetLayout();
          buildDefaultLayout(dockApi, { hasSliced });
        }}
      />
      <div
        ref={wrapRef}
        className={locked ? "w-full tdmm-dock-locked" : "w-full"}
        style={{ height: height ?? "calc(100svh - 10rem)" }}
        data-testid="model-detail-dock">
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
