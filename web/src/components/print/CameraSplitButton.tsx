import { ChevronDownIcon, VideoIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { openCameraWindow } from "@/lib/camWall";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";

/** "Camera ▾" split button: primary = inline view, menu = standalone window. */
export function CameraSplitButton({ printerId, onInline }: { printerId: number; onInline?: () => void }) {
  return (
    <div className="inline-flex">
      <Button type="button" variant="outline" size="sm" className="rounded-r-none" onClick={onInline}>
        <VideoIcon className="size-3.5" />
        Camera
      </Button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button type="button" variant="outline" size="sm" className="rounded-l-none border-l-0 px-1.5" aria-label="Camera options">
            <ChevronDownIcon className="size-3.5" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onSelect={() => openCameraWindow(printerId)}>Open in window</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => window.open("/camwall", "camwall", "popup")}>Open camera wall</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
