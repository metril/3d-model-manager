import { Grid3x3Icon } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { QUICK_COLORS } from "@/components/viewer/quickColors";
import { SegmentedControl } from "@/components/viewer/SegmentedControl";
import type { CameraPreset, Shading, ViewerToolsState } from "@/components/viewer/tools";
import { useTranslation } from "react-i18next";

// R10 camera presets, moved here from `ViewerStage`'s right panel -- see that
// file's prior comment for why `null` (no preset active) is excluded from
// the segmented control's own OPTIONS.
type CameraPresetOption = Exclude<CameraPreset, null>;
const CAMERA_PRESET_OPTIONS: readonly CameraPresetOption[] = ["iso", "top", "front", "side"];
const CAMERA_PRESET_KEYS: Record<CameraPresetOption, string> = {
  iso: "viewer.cam.iso",
  top: "viewer.cam.top",
  front: "viewer.cam.front",
  side: "viewer.cam.side",
};

const SHADING_OPTIONS: readonly Shading[] = ["solid", "wireframe", "xray"];
const SHADING_KEYS: Record<Shading, string> = {
  solid: "viewer.shade.solid",
  wireframe: "viewer.shade.wireframe",
  xray: "viewer.shade.xray",
};

/** Bottom-center floating dock (R13a GyroidVault re-chrome): camera preset,
 * shading (replaces the old separate Wireframe/X-ray toggle buttons with one
 * three-way segmented control over the same `tools.shading` enum), the 7
 * quick-color swatches (bulk-write via `onQuickColor`, Key decision 2), the
 * Grid toggle, and the `morePanel` slot (the "More" trigger + its Popover/
 * Sheet, rendered by the caller so this component stays a pure layout shell).
 * `stopPropagation` on keydown keeps typing/arrow-keys inside the dock from
 * also firing the canvas's `F`/`R`/`W`/`G` hotkeys (risk resolution 3). */
export function ViewerDock({
  tools,
  onToolsChange,
  checkedList,
  onQuickColor,
  morePanel,
}: {
  tools: ViewerToolsState;
  onToolsChange: (patch: Partial<ViewerToolsState>) => void;
  checkedList: number[];
  onQuickColor: (hex: string) => void;
  morePanel: ReactNode;
}) {
  const { t } = useTranslation();
  const cameraLabels = Object.fromEntries(
    CAMERA_PRESET_OPTIONS.map((o) => [o, t(CAMERA_PRESET_KEYS[o])]),
  ) as Record<CameraPresetOption, string>;
  const shadingLabels = Object.fromEntries(SHADING_OPTIONS.map((o) => [o, t(SHADING_KEYS[o])])) as Record<
    Shading,
    string
  >;
  const handleGridToggle = () => onToolsChange({ grid: !tools.grid });

  return (
    // Local `TooltipProvider` (mirrors `BackgroundSwatches`) keeps this
    // component self-sufficient for standalone rendering in
    // `ViewerDock.test.tsx` -- nesting inside `ViewerStage`'s own provider is
    // harmless, the nearest one just wins for this subtree.
    <TooltipProvider>
    <div
      className="pointer-events-auto flex max-w-[calc(100%-1rem)] flex-nowrap items-center gap-1 overflow-x-auto rounded-full border border-border bg-card/95 px-2 py-1.5 shadow-lg backdrop-blur-sm [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      onKeyDown={(event) => event.stopPropagation()}
    >
      <SegmentedControl
        label={t("viewer.cam.label")}
        options={CAMERA_PRESET_OPTIONS}
        labels={cameraLabels}
        value={tools.cameraPreset}
        onChange={(preset) => onToolsChange({ cameraPreset: preset })}
      />
      <SegmentedControl
        label={t("viewer.shade.label")}
        options={SHADING_OPTIONS}
        labels={shadingLabels}
        value={tools.shading}
        onChange={(shading) => onToolsChange({ shading })}
      />
      <div role="group" aria-label={t("viewer.dock.quickColors")} className="flex shrink-0 items-center gap-1 px-1">
        {QUICK_COLORS.map((hex) => (
          <button
            key={hex}
            type="button"
            aria-label={t("viewer.dock.paint", { hex })}
            disabled={checkedList.length === 0}
            onClick={() => onQuickColor(hex)}
            style={{ background: hex }}
            className="size-4 shrink-0 cursor-pointer rounded-full border border-black/25 shadow-sm outline-none transition-shadow focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/30"
          />
        ))}
      </div>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant={tools.grid ? "secondary" : "outline"}
            size="icon-sm"
            aria-pressed={tools.grid}
            aria-label={t("viewer.dock.grid")}
            onClick={handleGridToggle}
          >
            <Grid3x3Icon />
          </Button>
        </TooltipTrigger>
        <TooltipContent>{t("viewer.dock.gridTip")}</TooltipContent>
      </Tooltip>
      {morePanel}
    </div>
    </TooltipProvider>
  );
}
