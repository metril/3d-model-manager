import { ChevronDownIcon, VideoIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { openCameraWindow } from "@/lib/camWall";
import { floatingCamera } from "@/lib/floatingCamera";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";

/** Camera split button (icon; tooltip "Camera"): primary = inline view, menu = standalone window. */
export function CameraSplitButton({
  printerId,
  onInline,
  showLabel = false,
}: {
  printerId: number;
  onInline?: () => void;
  showLabel?: boolean;
}) {
  return (
    <div className="inline-flex">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size={showLabel ? "sm" : "icon-sm"}
            className="rounded-r-none"
            aria-label="Camera"
            onClick={onInline}
          >
            <VideoIcon />
            {showLabel ? "Camera" : null}
          </Button>
        </TooltipTrigger>
        <TooltipContent>Camera</TooltipContent>
      </Tooltip>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button type="button" variant="ghost" size="icon-xs" className="-ml-0.5 w-4 rounded-l-none" aria-label="Camera options">
            <ChevronDownIcon className="size-3.5" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onSelect={() => floatingCamera.toggle(printerId)}>Floating camera</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => openCameraWindow(printerId)}>Open in window</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => window.open("/camwall", "camwall", "popup")}>Open camera wall</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
