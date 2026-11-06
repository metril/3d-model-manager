import { CameraIcon, ChevronDownIcon, MaximizeIcon, MinimizeIcon, RotateCwIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { PartsChecklist } from "@/components/model-detail/FileRail";
import { formatStats, type SceneStats } from "@/components/viewer/tools";
import type { PartColors } from "@/components/viewer/partColors";
import type { FileOut } from "@/api/types";
import { useTranslation } from "react-i18next";

/** Top overlay (R13a GyroidVault re-chrome): the "how big is this print"
 * dims pill on the left, and on the right the Parts popover (hosting the
 * SAME `PartsChecklist` rows the old right panel used -- same aria-labels),
 * Spin (`tools.autoRotate`), Cover, and Fullscreen. Rendered absolutely over
 * the canvas by `ViewerStage`; this component's own root is
 * `pointer-events-none` with `pointer-events-auto` islands so orbit drags
 * still reach the canvas everywhere else (risk resolution 3). */
export function ViewerTopOverlay({
  stats,
  files,
  checkedIds,
  onToggleFile,
  onSetAllChecked,
  colors,
  onSetPartColor,
  onClearPartColor,
  autoRotate,
  onToggleAutoRotate,
  onCaptureCover,
  capturingCover,
  canCaptureCover,
  isFullscreen,
  onToggleFullscreen,
  container,
}: {
  stats: SceneStats | null;
  files: FileOut[];
  checkedIds: ReadonlySet<number>;
  onToggleFile: (fileId: number, checked: boolean) => void;
  onSetAllChecked: (checked: boolean) => void;
  colors: PartColors;
  onSetPartColor: (fileId: number, hex: string) => void;
  onClearPartColor: (fileId: number) => void;
  autoRotate: boolean;
  onToggleAutoRotate: () => void;
  onCaptureCover: () => void;
  capturingCover: boolean;
  canCaptureCover: boolean;
  isFullscreen: boolean;
  onToggleFullscreen: () => void;
  container: HTMLElement | null;
}) {
  const { t } = useTranslation();
  return (
    // Local `TooltipProvider` (mirrors `BackgroundSwatches`/`ViewerDock`)
    // keeps this component self-sufficient for standalone rendering in
    // `ViewerTopOverlay.test.tsx`.
    <TooltipProvider>
    <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-2 p-2">
      <div className="pointer-events-none rounded-full bg-background/80 px-3 py-1 text-xs whitespace-nowrap text-muted-foreground backdrop-blur-sm">
        {stats ? formatStats(stats) : t("viewer.noPartsSelected")}
      </div>
      <div
        className="pointer-events-auto flex items-center gap-1.5"
        onKeyDown={(event) => event.stopPropagation()}
      >
        {files.length > 0 && (
          <Popover>
            <PopoverTrigger asChild>
              <Button type="button" variant="outline" size="sm">
                {t("viewer.top.parts")}
                <ChevronDownIcon />
              </Button>
            </PopoverTrigger>
            <PopoverContent
              container={container}
              align="end"
              className="max-h-[70vh] w-80 overflow-y-auto"
              onKeyDown={(event) => event.stopPropagation()}
            >
              <PartsChecklist
                glbFiles={files}
                checkedIds={checkedIds}
                onToggleFile={onToggleFile}
                onSetAllChecked={onSetAllChecked}
                colors={colors}
                onSetPartColor={onSetPartColor}
                onClearPartColor={onClearPartColor}
              />
            </PopoverContent>
          </Popover>
        )}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant={autoRotate ? "secondary" : "outline"}
              size="icon-sm"
              aria-pressed={autoRotate}
              aria-label={t("viewer.autoRotate")}
              onClick={onToggleAutoRotate}
            >
              <RotateCwIcon />
            </Button>
          </TooltipTrigger>
          <TooltipContent>{t("viewer.top.spin")}</TooltipContent>
        </Tooltip>
        {canCaptureCover && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="outline"
                size="icon-sm"
                aria-label={t("viewer.top.cover")}
                disabled={capturingCover}
                onClick={onCaptureCover}
              >
                <CameraIcon />
              </Button>
            </TooltipTrigger>
            <TooltipContent>{t("viewer.top.setCover")}</TooltipContent>
          </Tooltip>
        )}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="outline"
              size="icon-sm"
              aria-pressed={isFullscreen}
              aria-label={t("viewer.fullscreen")}
              onClick={onToggleFullscreen}
            >
              {isFullscreen ? <MinimizeIcon /> : <MaximizeIcon />}
            </Button>
          </TooltipTrigger>
          <TooltipContent>{t("viewer.top.fullscreenTip")}</TooltipContent>
        </Tooltip>
      </div>
    </div>
    </TooltipProvider>
  );
}
