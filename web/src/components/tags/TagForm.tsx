import { useState } from "react";
import { CheckIcon } from "lucide-react";

import { TAG_ICONS, TAG_ICON_NAMES } from "@/components/tags/tagIcons";
import { TagChip } from "@/components/tags/TagChip";
import { Button } from "@/components/ui/button";
import { ColorField } from "@/components/ui/color-field";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { DEFAULT_TAG_COLOR, TAG_PALETTE, isHexColor, readableTextColor } from "@/lib/tagColors";
import { cn } from "@/lib/utils";

export interface TagFormValues {
  name: string;
  color: string | null;
  icon: string | null;
  description: string | null;
}

/** Palette quick-picks + colour picker popover. */
export function TagColorField({ value, onChange }: { value: string | null; onChange: (hex: string) => void }) {
  const current = isHexColor(value) ? value.toLowerCase() : null;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Color">
        {TAG_PALETTE.map(({ key, hex }) => (
          <button
            key={key}
            type="button"
            aria-label={`Color ${key}`}
            aria-pressed={current === hex}
            onClick={() => onChange(hex)}
            style={{ backgroundColor: hex, color: readableTextColor(hex) }}
            className={cn(
              "flex size-5 items-center justify-center rounded-full ring-offset-2 ring-offset-popover",
              current === hex && "ring-2 ring-foreground",
            )}
          >
            {current === hex && <CheckIcon className="size-3" />}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <ColorField
          label="Pick custom color"
          value={current ?? ""}
          onChange={onChange}
          swatches={[]}
          clearable={false}
          className="w-32"
        />
      </div>
    </div>
  );
}

/** Grid of the curated lucide subset; click again to clear. */
export function TagIconField({ value, onChange }: { value: string | null; onChange: (icon: string | null) => void }) {
  return (
    <div className="grid max-h-28 grid-cols-8 gap-1 overflow-y-auto" role="group" aria-label="Icon">
      {TAG_ICON_NAMES.map((name) => {
        const Icon = TAG_ICONS[name]!;
        return (
          <button
            key={name}
            type="button"
            title={name}
            aria-label={`Icon ${name}`}
            aria-pressed={value === name}
            onClick={() => onChange(value === name ? null : name)}
            className={cn(
              "flex size-6 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground",
              value === name && "bg-primary text-primary-foreground hover:bg-primary hover:text-primary-foreground",
            )}
          >
            <Icon className="size-3.5" />
          </button>
        );
      })}
    </div>
  );
}

/** Name + color + icon + description, with a live preview chip. Used for
 * both creating a tag inline (picker) and editing one (Tag Manager). */
export function TagForm({
  initial,
  submitLabel,
  pending,
  onSubmit,
  onCancel,
}: {
  initial: Partial<TagFormValues>;
  submitLabel: string;
  pending?: boolean;
  onSubmit: (values: TagFormValues) => void;
  onCancel?: () => void;
}) {
  const [name, setName] = useState(initial.name ?? "");
  const [color, setColor] = useState<string | null>(initial.color ?? DEFAULT_TAG_COLOR);
  const [icon, setIcon] = useState<string | null>(initial.icon ?? null);
  const [description, setDescription] = useState(initial.description ?? "");
  const trimmed = name.trim();

  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (!trimmed || pending) return;
        onSubmit({ name: trimmed, color, icon, description: description.trim() || null });
      }}
    >
      <Input aria-label="Tag name" placeholder="Name" value={name} onChange={(event) => setName(event.target.value)} />
      <TagColorField value={color} onChange={setColor} />
      <TagIconField value={icon} onChange={setIcon} />
      <Textarea
        aria-label="Tag description"
        placeholder="Description (optional)"
        rows={2}
        value={description}
        onChange={(event) => setDescription(event.target.value)}
      />
      <div className="flex items-center justify-between gap-2">
        <TagChip tag={{ name: trimmed || "Preview", color, icon, description: null }} />
        <div className="flex gap-1.5">
          {onCancel && (
            <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
              Cancel
            </Button>
          )}
          <Button type="submit" size="sm" disabled={!trimmed || pending}>
            {submitLabel}
          </Button>
        </div>
      </div>
    </form>
  );
}
