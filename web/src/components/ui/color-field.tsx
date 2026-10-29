/** Colour picker field: a swatch button that opens a popover with a
 * saturation/hue picker, hex input, eyedropper, quick swatches and
 * (optionally) the manufacturer colour catalog. */
import { useState } from "react";
import { PipetteIcon, XIcon } from "lucide-react";
import { HexColorPicker } from "react-colorful";

import type { ColorCatalogEntry } from "@/api/types";
import { ColorCatalogPicker } from "@/components/inventory/ColorCatalogPicker";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { TAG_PALETTE, normalizeHex } from "@/lib/tagColors";
import { cn } from "@/lib/utils";

interface EyeDropperLike {
  open: () => Promise<{ sRGBHex: string }>;
}
type EyeDropperCtor = new () => EyeDropperLike;

const FALLBACK = "#808080";

export interface ColorFieldProps {
  value: string;
  onChange: (hex: string) => void;
  /** Colour name; set via `onNameChange` when a catalog entry is picked. */
  name?: string;
  onNameChange?: (name: string) => void;
  /** Quick-pick hex values; defaults to the tag palette. */
  swatches?: readonly string[];
  /** Show the manufacturer colour catalog search inside the popover. */
  catalog?: boolean;
  /** Override what picking a catalog entry does (default: hex + name). */
  onCatalogPick?: (entry: ColorCatalogEntry) => void;
  label: string;
  id?: string;
  disabled?: boolean;
  /** Offer a "clear" button (for optional colours). Default true. */
  clearable?: boolean;
  className?: string;
}

export function ColorField({
  value,
  onChange,
  onNameChange,
  swatches,
  catalog,
  onCatalogPick,
  label,
  id,
  disabled,
  clearable = true,
  className,
}: ColorFieldProps) {
  const current = normalizeHex(value);
  const [hexText, setHexText] = useState(current ?? value);
  const [prev, setPrev] = useState(current);
  // Re-sync the text box when the colour changes from outside (picker drag, swatch, catalog).
  if (current !== prev) {
    setPrev(current);
    setHexText(current ?? value);
  }
  const quick = swatches ?? TAG_PALETTE.map((p) => p.hex);
  const eyeDropper =
    typeof window !== "undefined" ? (window as unknown as { EyeDropper?: EyeDropperCtor }).EyeDropper : undefined;

  async function pipette() {
    if (!eyeDropper) return;
    try {
      const res = await new eyeDropper().open();
      const hex = normalizeHex(res.sRGBHex);
      if (hex) onChange(hex);
    } catch {
      /* user cancelled */
    }
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          id={id}
          aria-label={label}
          disabled={disabled}
          className={cn(
            "flex h-8 w-full items-center gap-2 rounded-lg border border-input bg-transparent px-2 text-left text-sm outline-none transition-colors hover:bg-muted focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50",
            className,
          )}
        >
          <span
            aria-hidden="true"
            data-testid="color-swatch"
            className="size-5 shrink-0 rounded-full border border-border/60"
            style={
              current
                ? { backgroundColor: current }
                : {
                    backgroundImage: "repeating-conic-gradient(var(--muted-foreground) 0% 25%, transparent 0% 50%)",
                    backgroundSize: "8px 8px",
                    opacity: 0.4,
                  }
            }
          />
          <span className={cn("font-mono text-xs", !current && "text-muted-foreground")}>{current ?? "#RRGGBB"}</span>
        </button>
      </PopoverTrigger>
      <PopoverContent side="bottom" align="start" sideOffset={6} collisionPadding={12} avoidCollisions className="max-h-[min(28rem,var(--radix-popover-content-available-height))] w-64 overflow-y-auto">
        <HexColorPicker color={current ?? FALLBACK} onChange={onChange} style={{ width: "100%", height: 160 }} />
        <div className="flex items-center gap-1.5">
          <Input
            aria-label="Hex color"
            placeholder="#rrggbb"
            value={hexText}
            maxLength={7}
            autoComplete="off"
            className="h-7 flex-1 font-mono text-xs"
            onChange={(e) => {
              setHexText(e.target.value);
              const hex = normalizeHex(e.target.value);
              if (hex) onChange(hex);
            }}
          />
          {eyeDropper ? (
            <Button type="button" variant="outline" size="icon-sm" aria-label="Pick colour from screen" onClick={pipette}>
              <PipetteIcon />
            </Button>
          ) : null}
          {clearable && value ? (
            <Button
              type="button"
              variant="outline"
              size="icon-sm"
              aria-label="Clear colour"
              onClick={() => {
                setHexText("");
                onChange("");
              }}
            >
              <XIcon />
            </Button>
          ) : null}
        </div>
        {quick.length > 0 ? (
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Quick colours">
            {quick.map((hex) => (
              <button
                key={hex}
                type="button"
                aria-label={`Color ${hex}`}
                aria-pressed={current === hex.toLowerCase()}
                onClick={() => onChange(hex)}
                style={{ backgroundColor: hex }}
                className={cn(
                  "size-5 rounded-full border border-border/60 ring-offset-2 ring-offset-popover",
                  current === hex.toLowerCase() && "ring-2 ring-foreground",
                )}
              />
            ))}
          </div>
        ) : null}
        {catalog ? (
          <ColorCatalogPicker
            onPick={(entry) => {
              if (onCatalogPick) onCatalogPick(entry);
              else {
                onChange(entry.hex);
                onNameChange?.(entry.color_name);
              }
            }}
          />
        ) : null}
      </PopoverContent>
    </Popover>
  );
}
