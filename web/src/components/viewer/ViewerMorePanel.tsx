import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ExternalLinkIcon, MoreHorizontalIcon, RotateCcwIcon, ScanIcon, BoxIcon, CameraIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { FilamentChip } from "@/components/ui/filament-chip";
import { usePrinterStatus } from "@/api/printers";
import { BackgroundSwatches } from "@/components/viewer/BackgroundSwatches";
import { SegmentedControl } from "@/components/viewer/SegmentedControl";
import type { BackgroundPreset } from "@/components/viewer/background";
import { type ExplodeMode } from "@/components/viewer/explode";
import {
  LIGHTING_PRESET_ORDER,
  type LightingPreset,
} from "@/components/viewer/lighting";
import { traysToPartColors, type PartColors } from "@/components/viewer/partColors";
import type { SectionAxis, ViewerToolsState } from "@/components/viewer/tools";

const SECTION_AXIS_OPTIONS: readonly SectionAxis[] = ["x", "y", "z"];
const SECTION_AXIS_LABELS: Record<SectionAxis, string> = { x: "X", y: "Y", z: "Z" };

/** `useIsWideViewport`-style hook, plain `window.innerWidth` + a resize
 * listener rather than `matchMedia` -- jsdom (this project's vitest
 * environment) doesn't implement `matchMedia` at all, so a resize-based
 * check keeps `ViewerMorePanel.test.tsx` able to drive both breakpoints by
 * just setting `window.innerWidth` and firing `resize`. */
function useIsWide(minWidth: number): boolean {
  const [wide, setWide] = useState(() => (typeof window === "undefined" ? true : window.innerWidth >= minWidth));
  useEffect(() => {
    if (typeof window === "undefined") return;
    const handle = () => setWide(window.innerWidth >= minWidth);
    handle();
    window.addEventListener("resize", handle);
    return () => window.removeEventListener("resize", handle);
  }, [minWidth]);
  return wide;
}

/** AMS filament legend + "Sync colors from printer" (M8 G3), moved here
 * unchanged from the old `ViewerStage` right panel. Rendered only when a
 * printer is configured. Maps the checked parts onto the loaded trays in
 * order, cycling if there are more parts than trays. */
function AmsSync({
  printerId,
  partIds,
  onApply,
}: {
  printerId: number;
  partIds: number[];
  onApply: (colors: PartColors) => void;
}) {
  const { t } = useTranslation();
  const status = usePrinterStatus(printerId);
  const trays = (status.data?.trays ?? []).filter((tray) => tray.color);
  if (trays.length === 0) return null;

  return (
    <div className="flex flex-col gap-2">
      <span className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{t("viewer.panel.loadedFilament")}</span>
      <div className="flex flex-wrap gap-1.5">
        {trays.map((tray) => (
          <FilamentChip key={tray.slot} color={tray.color ?? undefined} material={tray.material ?? undefined} />
        ))}
      </div>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={partIds.length === 0}
        onClick={() => onApply(traysToPartColors(partIds, trays))}
        className="w-full"
      >
        {t("viewer.panel.syncColors")}
      </Button>
    </div>
  );
}

export interface ViewerMorePanelProps {
  preset: BackgroundPreset;
  custom: string;
  onPresetChange: (preset: BackgroundPreset) => void;
  onCustomChange: (custom: string) => void;
  lightingPreset: LightingPreset;
  onLightingChange: (preset: LightingPreset) => void;
  tools: ViewerToolsState;
  onToolsChange: (patch: Partial<ViewerToolsState>) => void;
  explodeMode: ExplodeMode;
  onFit: () => void;
  onScreenshot: () => void;
  hasColors: boolean;
  onResetColors: () => void;
  printerId: number | undefined;
  checkedList: number[];
  onApplyAmsColors: (colors: PartColors) => void;
  onOpenWindow: (ids: number[]) => void;
  showWindowButtons: boolean;
  /** Portal target for the Popover/Sheet content -- the fullscreened stage
   * element, so the panel survives fullscreen (risk resolution 2). */
  container: HTMLElement | null;
}

/** Body shared by both the Popover (>=900px) and Sheet (<900px) shells --
 * every control the old `ViewerStage` right panel had that isn't already in
 * `ViewerTopOverlay` (Parts, Spin, Cover, Fullscreen) or `ViewerDock`
 * (camera preset, shading, quick colors, Grid). */
function MorePanelBody({
  preset,
  custom,
  onPresetChange,
  onCustomChange,
  lightingPreset,
  onLightingChange,
  tools,
  onToolsChange,
  explodeMode,
  onFit,
  onScreenshot,
  hasColors,
  onResetColors,
  printerId,
  checkedList,
  onApplyAmsColors,
  onOpenWindow,
  showWindowButtons,
}: Omit<ViewerMorePanelProps, "container">) {
  const { t } = useTranslation();
  const handleOrthoToggle = () => {
    onToolsChange({ ortho: !tools.ortho });
    onFit();
  };
  const lightingLabels = Object.fromEntries(
    LIGHTING_PRESET_ORDER.map((o) => [o, t(`viewer.light.${o}`)]),
  ) as Record<LightingPreset, string>;
  const explodeLabel = explodeMode === "explode" ? t("viewer.panel.explode") : t("viewer.panel.separate");
  const handleAutoRotateToggle = () => onToolsChange({ autoRotate: !tools.autoRotate });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <span className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{t("viewer.bg.label")}</span>
        <BackgroundSwatches preset={preset} custom={custom} onPresetChange={onPresetChange} onCustomChange={onCustomChange} />
      </div>

      <div className="flex flex-col gap-1.5">
        <span className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{t("viewer.light.label")}</span>
        <SegmentedControl
          label={t("viewer.light.label")}
          options={LIGHTING_PRESET_ORDER}
          labels={lightingLabels}
          value={lightingPreset}
          onChange={onLightingChange}
          className="flex-wrap"
        />
      </div>

      <div className="flex flex-col gap-3">
        <Label className="flex items-center gap-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
          <Checkbox
            checked={tools.section.enabled}
            onCheckedChange={(next) => onToolsChange({ section: { ...tools.section, enabled: next === true } })}
          />
          {t("viewer.panel.section")}
        </Label>
        {tools.section.enabled && (
          <div className="flex flex-col gap-2 pl-6">
            <div className="flex flex-col gap-1.5">
              <span className="text-xs text-muted-foreground">{t("viewer.panel.axis")}</span>
              <SegmentedControl
                label={t("viewer.panel.axis")}
                options={SECTION_AXIS_OPTIONS}
                labels={SECTION_AXIS_LABELS}
                value={tools.section.axis}
                onChange={(axis) => onToolsChange({ section: { ...tools.section, axis } })}
              />
            </div>
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              aria-label={t("viewer.panel.sectionPosition")}
              value={tools.section.t}
              onChange={(event) => onToolsChange({ section: { ...tools.section, t: Number(event.target.value) } })}
              className="w-full"
            />
          </div>
        )}
      </div>

      {explodeMode !== "none" && (
        <div className="flex flex-col gap-3">
          <span className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            {explodeLabel}
          </span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.01}
            aria-label={explodeLabel}
            value={tools.explode}
            onChange={(event) => onToolsChange({ explode: Number(event.target.value) })}
            className="w-full"
          />
        </div>
      )}

      <div className="flex flex-wrap items-center gap-1.5">
        <Button
          type="button"
          variant={tools.ortho ? "secondary" : "outline"}
          size="sm"
          aria-pressed={tools.ortho}
          aria-label={t("viewer.panel.orthoCamera")}
          onClick={handleOrthoToggle}
        >
          <BoxIcon />
          {t("viewer.panel.ortho")}
        </Button>
        <Button type="button" variant="outline" size="sm" aria-label={t("viewer.panel.fitView")} onClick={onFit}>
          <ScanIcon />
          {t("viewer.panel.fit")}
        </Button>
        <Button
          type="button"
          variant={tools.autoRotate ? "secondary" : "outline"}
          size="sm"
          aria-pressed={tools.autoRotate}
          aria-label={t("viewer.autoRotate")}
          onClick={handleAutoRotateToggle}
        >
          {t("viewer.autoRotate")}
        </Button>
        <Button type="button" variant="outline" size="sm" aria-label={t("viewer.panel.screenshot")} onClick={onScreenshot}>
          <CameraIcon />
          {t("viewer.panel.screenshot")}
        </Button>
      </div>

      {printerId !== undefined && <AmsSync printerId={printerId} partIds={checkedList} onApply={onApplyAmsColors} />}

      {hasColors && (
        <Button type="button" variant="ghost" size="sm" className="w-full" onClick={onResetColors}>
          <RotateCcwIcon />
          {t("viewer.panel.resetColors")}
        </Button>
      )}

      {showWindowButtons && (
        <div className="flex flex-col gap-1.5">
          <Button type="button" variant="outline" size="sm" disabled={checkedList.length === 0} onClick={() => onOpenWindow(checkedList)}>
            <ExternalLinkIcon />
            {t("viewer.panel.newWindow")}
          </Button>
          {checkedList.length > 1 && (
            <Button type="button" variant="outline" size="sm" onClick={() => checkedList.forEach((id) => onOpenWindow([id]))}>
              {t("viewer.panel.partsInWindows")}
            </Button>
          )}
        </div>
      )}

      <div className="flex flex-col gap-1 border-t border-border pt-3 text-xs text-muted-foreground max-sm:hidden [@media(hover:none)]:hidden">
        <span className="font-medium tracking-wide uppercase">{t("viewer.hotkeys")}</span>
        <span>{t("viewer.hotkeyLegend")}</span>
        <span data-testid="plate-hotkeys">{t("viewer.hotkeyPlates")}</span>
      </div>
    </div>
  );
}

/** The "More" trigger + its Popover (>=900px)/Sheet (<900px) content --
 * portalled into `container` (the fullscreened stage element) so it survives
 * fullscreen (risk resolution 2). Everything the old right panel had that
 * doesn't live in `ViewerTopOverlay`/`ViewerDock` now lives here. */
export function ViewerMorePanel(props: ViewerMorePanelProps): ReactNode {
  const { t } = useTranslation();
  const wide = useIsWide(900);
  const { container, ...bodyProps } = props;

  if (wide) {
    return (
      <Popover>
        <PopoverTrigger asChild>
          <Button type="button" variant="outline" size="icon-sm" aria-label={t("viewer.panel.more")}>
            <MoreHorizontalIcon />
          </Button>
        </PopoverTrigger>
        <PopoverContent
          container={container}
          side="top"
          align="end"
          collisionBoundary={container ?? undefined}
          collisionPadding={8}
          className="max-h-[var(--radix-popover-content-available-height)] w-80 max-w-[var(--radix-popover-content-available-width)] overflow-y-auto"
          onKeyDown={(event) => event.stopPropagation()}
        >
          <MorePanelBody {...bodyProps} />
        </PopoverContent>
      </Popover>
    );
  }

  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button type="button" variant="outline" size="icon-sm" aria-label={t("viewer.panel.more")}>
          <MoreHorizontalIcon />
        </Button>
      </SheetTrigger>
      <SheetContent
        container={container}
        side="bottom"
        className="max-h-[80%] overflow-y-auto"
        onKeyDown={(event) => event.stopPropagation()}
      >
        <SheetTitle className="sr-only">{t("viewer.panel.options")}</SheetTitle>
        <MorePanelBody {...bodyProps} />
      </SheetContent>
    </Sheet>
  );
}
