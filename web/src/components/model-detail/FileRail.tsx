import { RotateCcwIcon } from "lucide-react";

import { TagChips } from "@/components/tags/TagChip";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { FilamentChip } from "@/components/ui/filament-chip";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { PartColors } from "@/components/viewer/partColors";
import type { FileOut } from "@/api/types";

/** One part's visibility checkbox + color swatch + reset button -- the exact
 * row markup the old `ViewerStage` right panel's Parts checklist used
 * (aria-labels unchanged: `file.rel_path`, `Color for ${file.rel_path}`,
 * `Reset color for ${file.rel_path}`), used by `ViewerTopOverlay`'s Parts
 * popover via `PartsChecklist` below. */
export function PartRow({
  file,
  checked,
  color,
  onToggle,
  onSetColor,
  onClearColor,
}: {
  file: FileOut;
  checked: boolean;
  color: string | undefined;
  onToggle: (checked: boolean) => void;
  onSetColor: (hex: string) => void;
  onClearColor: () => void;
}) {
  return (
    <div className={cn("flex items-center gap-2", !checked && "opacity-60")}>
      <Checkbox checked={checked} onCheckedChange={(next) => onToggle(next === true)} aria-label={file.rel_path} />
      <label className="relative inline-flex size-6 shrink-0 cursor-pointer items-center justify-center rounded-full focus-within:ring-2 focus-within:ring-ring/50">
        <FilamentChip color={color ?? "#cccccc"} />
        <input
          type="color"
          aria-label={`Color for ${file.rel_path}`}
          value={color ?? "#cccccc"}
          onChange={(event) => onSetColor(event.target.value)}
          className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
        />
      </label>
      <span className="min-w-0 flex-1 truncate text-sm" title={file.rel_path}>
        {file.rel_path}
      </span>
      {file.tag_refs && file.tag_refs.length > 0 && <TagChips tags={file.tag_refs} max={2} className="shrink-0 flex-nowrap" />}
      {color && (
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              aria-label={`Reset color for ${file.rel_path}`}
              onClick={onClearColor}
              className="inline-flex size-6 shrink-0 items-center justify-center rounded-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
            >
              <RotateCcwIcon className="size-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent>Reset color</TooltipContent>
        </Tooltip>
      )}
    </div>
  );
}

interface PartRowListProps {
  glbFiles: FileOut[];
  checkedIds: ReadonlySet<number>;
  onToggleFile: (fileId: number, checked: boolean) => void;
  colors: PartColors;
  onSetPartColor: (fileId: number, hex: string) => void;
  onClearPartColor: (fileId: number) => void;
}

/** One `PartRow` per part, no header -- shared by `PartsChecklist` below. */
export function PartRowList({
  glbFiles,
  checkedIds,
  onToggleFile,
  colors,
  onSetPartColor,
  onClearPartColor,
}: PartRowListProps) {
  return (
    <div className="space-y-1">
      {glbFiles.map((file) => (
        <PartRow
          key={file.id}
          file={file}
          checked={checkedIds.has(file.id)}
          color={colors[file.id]}
          onToggle={(checked) => onToggleFile(file.id, checked)}
          onSetColor={(hex) => onSetPartColor(file.id, hex)}
          onClearColor={() => onClearPartColor(file.id)}
        />
      ))}
    </div>
  );
}

/** Count + All/None header (same aria-labels as the old `ViewerStage` right
 * panel: "Show all parts", "None — hide all parts") followed by
 * `PartRowList` -- the whole Parts checklist block used by
 * `ViewerTopOverlay`'s Parts popover (R13a re-chrome). */
export function PartsChecklist({
  glbFiles,
  checkedIds,
  onToggleFile,
  onSetAllChecked,
  colors,
  onSetPartColor,
  onClearPartColor,
}: PartRowListProps & { onSetAllChecked: (checked: boolean) => void }) {
  const checkedCount = glbFiles.filter((file) => checkedIds.has(file.id)).length;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground tabular-nums">
          {checkedCount} of {glbFiles.length}
        </span>
        <div className="flex items-center gap-1">
          <Button
            type="button"
            variant="ghost"
            size="xs"
            aria-label="Show all parts"
            disabled={checkedCount === glbFiles.length}
            onClick={() => onSetAllChecked(true)}
          >
            All
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="xs"
            aria-label="None — hide all parts"
            disabled={checkedCount === 0}
            onClick={() => onSetAllChecked(false)}
          >
            None
          </Button>
        </div>
      </div>
      <PartRowList
        glbFiles={glbFiles}
        checkedIds={checkedIds}
        onToggleFile={onToggleFile}
        colors={colors}
        onSetPartColor={onSetPartColor}
        onClearPartColor={onClearPartColor}
      />
    </div>
  );
}
