import { useEffect, useRef, useState } from "react";
import { PencilIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

interface InlineEditProps {
  value: string;
  placeholder?: string;
  multiline?: boolean;
  onSave: (value: string) => void;
  className?: string;
  displayClassName?: string;
  "aria-label"?: string;
}

/** Explicit-commit click-to-edit text (name/description in `ModelHeader`,
 * mounted only while the page is in edit mode). Resting state shows the
 * value plus a small pencil affordance; opening it reveals an input/textarea
 * with explicit Save/Cancel actions. Blur does NOT save -- only Save (or
 * Enter on single-line, Cmd/Ctrl+Enter on multiline) commits; Cancel or
 * Escape reverts without saving.
 *
 * Renders `<span>`s (not `<div>`s) so callers can nest it inside phrasing
 * containers like `<h1>` and keep the heading in the a11y outline. */
export function InlineEdit({
  value,
  placeholder,
  multiline,
  onSave,
  className,
  displayClassName,
  "aria-label": ariaLabel,
}: InlineEditProps) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const triggerRef = useRef<HTMLButtonElement>(null);
  // Set when the editor closes via Save/Cancel/Escape so the effect below
  // returns focus to the pencil trigger (instead of dropping it on <body>).
  // Focus happens in an effect -- the trigger isn't mounted yet when
  // `setEditing(false)` runs -- and never on unmount, since the effect only
  // fires on a re-render that mounts the trigger again.
  const restoreFocus = useRef(false);

  useEffect(() => {
    if (!editing) setDraft(value);
  }, [value, editing]);

  useEffect(() => {
    if (!editing && restoreFocus.current) {
      restoreFocus.current = false;
      triggerRef.current?.focus();
    }
  }, [editing]);

  function commit() {
    const trimmed = draft.trim();
    restoreFocus.current = true;
    setEditing(false);
    if (trimmed !== value) onSave(trimmed);
  }

  function cancel() {
    setDraft(value);
    restoreFocus.current = true;
    setEditing(false);
  }

  if (editing) {
    const Field = multiline ? Textarea : Input;
    return (
      <span className="block space-y-1.5">
        <Field
          autoFocus
          value={draft}
          placeholder={placeholder}
          aria-label={ariaLabel}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            // An IME composition is confirmed with Enter; that keystroke
            // must not commit the draft.
            if (event.key === "Enter" && !multiline && !event.nativeEvent.isComposing) {
              event.preventDefault();
              commit();
            }
            if (event.key === "Enter" && multiline && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              commit();
            }
            if (event.key === "Escape") {
              event.preventDefault();
              cancel();
            }
          }}
          className={className}
        />
        <span className="flex gap-2">
          <Button type="button" size="sm" onClick={commit}>
            {t("inlineEdit.save")}
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={cancel}>
            {t("inlineEdit.cancel")}
          </Button>
        </span>
      </span>
    );
  }

  return (
    <span className="flex items-start gap-1.5">
      <span className={cn(!value && "text-muted-foreground", displayClassName)}>
        {value || placeholder}
      </span>
      <Button
        ref={triggerRef}
        type="button"
        variant="ghost"
        size="icon-xs"
        aria-label={ariaLabel ? t("inlineEdit.editNamed", { label: ariaLabel }) : t("inlineEdit.edit")}
        onClick={() => setEditing(true)}
        className="mt-0.5 shrink-0 opacity-60 hover:opacity-100"
      >
        <PencilIcon />
      </Button>
    </span>
  );
}
