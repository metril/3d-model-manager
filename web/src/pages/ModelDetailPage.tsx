import { useTranslation } from "react-i18next";
import { useMemo, useState } from "react";
import { getRouteApi } from "@tanstack/react-router";
import type { DockviewApi } from "dockview-react";

import { useModel } from "@/api/library";
import { ArchivedBanner } from "@/components/model-detail/ArchivedBanner";
import { ModelHeader } from "@/components/model-detail/ModelHeader";
import { useStudioSelection } from "@/components/model-detail/studioSelection";
import { ModelDetailDock } from "@/components/model-detail/dock/ModelDetailDock";
import { ModelDetailProvider } from "@/components/model-detail/dock/ModelDetailContext";
import { LayoutMenu } from "@/components/dock/LayoutMenu";
import { addPanelAtDefault, buildDefaultLayout } from "@/components/model-detail/dock/defaultLayout";
import { resetLayout } from "@/components/model-detail/dock/layoutStore";
import { PANEL_IDS, PANEL_TITLE_KEYS } from "@/components/model-detail/dock/panelMeta";
import { StackedPanels } from "@/components/model-detail/dock/StackedPanels";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/api/client";
import { useMediaQuery } from "@/lib/useMediaQuery";

const routeApi = getRouteApi("/authenticated/models/$slug");

export function ModelDetailPage() {
  const { slug } = routeApi.useParams();
  const modelQuery = useModel(slug);
  const { t } = useTranslation();
  // Per-visit UI state only -- not persisted. Gates metadata editing
  // (name/description/tags/archive) behind an explicit toggle so a stray
  // click can't silently mutate a model.
  const [editMode, setEditMode] = useState(false);
  // The "Move / copy…" trigger lives in `ModelHeader`'s overflow menu, but
  // the dialog it opens (`StorageLocationBar`) now renders inside
  // `TagsLinksCard` -- lifted here so both can share the same controlled
  // `open` state.
  const [relocateOpen, setRelocateOpen] = useState(false);
  // The route component stays mounted when only `$slug` changes (e.g. the
  // upcoming related-models strip links detail -> detail), so drop the gate
  // when navigating to a different model: landing on it already in edit
  // mode -- possibly with a stale open InlineEdit draft, which deliberately
  // never resets while editing -- would defeat the whole gate. Render-time
  // state adjustment (per React's "adjusting state when a prop changes")
  // instead of an effect, so the new model never paints editable.
  const [gateSlug, setGateSlug] = useState(slug);
  if (slug !== gateSlug) {
    setGateSlug(slug);
    setEditMode(false);
    setRelocateOpen(false);
  }
  // Called unconditionally (rules of hooks) ahead of the loading/error early
  // returns below -- guards `model` being absent internally instead. Owns
  // the studio surface's selection here (not inside `StudioWorkspace`) so
  // `FilesDocsCard`'s per-row "View in 3D" action can reach it too.
  const studio = useStudioSelection(modelQuery.data);
  // >=900px: dockable panels. Below: the stacked cards, no dock.
  const docked = useMediaQuery("(min-width: 900px)");
  const [dockApi, setDockApi] = useState<DockviewApi | null>(null);
  // Memoised so unrelated re-renders (dock api, media query) don't re-render
  // every context consumer, including the WebGL viewer stage.
  const model = modelQuery.data;
  const ctxValue = useMemo(
    () => (model ? { model, editMode, setEditMode, relocateOpen, setRelocateOpen, studio } : null),
    [model, editMode, relocateOpen, studio],
  );

  if (modelQuery.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-1/3" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (modelQuery.isError) {
    const message =
      modelQuery.error instanceof ApiError ? modelQuery.error.detail : t("mdPage.loadError");
    return <p className="text-sm text-destructive">{message}</p>;
  }

  if (!model || !ctxValue) return null;

  return (
    <ModelDetailProvider value={ctxValue}>
      <div className="space-y-6">
        <ModelHeader
          model={model}
          editMode={editMode}
          onToggleEditMode={() => setEditMode((prev) => !prev)}
          onOpenRelocate={() => setRelocateOpen(true)}
          panelsMenu={
            docked && dockApi ? (
              <LayoutMenu
                panels={{ api: dockApi, ids: PANEL_IDS, titleKeys: PANEL_TITLE_KEYS, add: addPanelAtDefault }}
                onReset={() => {
                  resetLayout();
                  buildDefaultLayout(dockApi);
                }}
              />
            ) : undefined
          }
        />
        <ArchivedBanner model={model} />
        {docked ? <ModelDetailDock onApi={setDockApi} /> : <StackedPanels />}
      </div>
    </ModelDetailProvider>
  );
}
