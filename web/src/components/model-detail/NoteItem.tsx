import { useTranslation } from "react-i18next";
import { useState } from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { formatDateTime } from "@/lib/format";
import { MARKDOWN_CLASSNAME, renderMarkdown } from "@/lib/markdown";
import type { NoteOut } from "@/api/types";

/** Single note row, shared by model-level (`NotesTab`) and per-revision
 * (`RevisionsTab`) notes UIs — view/edit/delete only; callers own the
 * create composer and the actual mutation wiring (their invalidation
 * targets differ). */
export function NoteItem({
  note,
  isSaving,
  onSave,
  onDelete,
}: {
  note: NoteOut;
  isSaving: boolean;
  onSave: (body: string, onSuccess: () => void) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const { t } = useTranslation();
  const [draft, setDraft] = useState(note.body);

  if (editing) {
    return (
      <li className="rounded-lg border border-border p-3">
        <Textarea value={draft} onChange={(event) => setDraft(event.target.value)} rows={3} autoFocus />
        <div className="mt-2 flex gap-2">
          <Button
            type="button"
            size="sm"
            disabled={isSaving || draft.trim().length === 0}
            onClick={() => onSave(draft, () => setEditing(false))}
          >
            {t("common.save")}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => {
              setDraft(note.body);
              setEditing(false);
            }}
          >
            {t("common.cancel")}
          </Button>
        </div>
      </li>
    );
  }

  return (
    <li className="rounded-lg border border-border p-3">
      <div className={MARKDOWN_CLASSNAME} dangerouslySetInnerHTML={{ __html: renderMarkdown(note.body) }} />
      <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
        <span>{formatDateTime(note.updated_at)}</span>
        <div className="flex gap-3">
          <button type="button" className="hover:underline" onClick={() => setEditing(true)}>
            {t("common.edit")}
          </button>
          <ConfirmDialog
            trigger={
              <button type="button" className="hover:underline">
                {t("common.delete")}
              </button>
            }
            title={t("mdNote.deleteTitle")}
            confirmLabel={t("common.delete")}
            destructive
            onConfirm={onDelete}
          />
        </div>
      </div>
    </li>
  );
}
