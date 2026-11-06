import { useTranslation } from "react-i18next";
import { useState } from "react";

import { useCreateNote, useDeleteNote, usePatchNote } from "@/api/library";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { NoteItem } from "@/components/model-detail/NoteItem";
import type { ModelDetail } from "@/api/types";

export function NotesTab({ model }: { model: ModelDetail }) {
  const [draft, setDraft] = useState("");
  const { t } = useTranslation();
  const createNote = useCreateNote(model.slug);
  const patchNote = usePatchNote(model.slug);
  const deleteNote = useDeleteNote(model.slug);

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const body = draft.trim();
    if (!body) return;
    createNote.mutate({ model_id: model.id, body }, { onSuccess: () => setDraft("") });
  }

  return (
    <div className="space-y-4">
      <form onSubmit={handleSubmit} className="space-y-2">
        <Textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={t("mdNotes.placeholder")}
          rows={3}
          aria-label={t("mdNotes.newNote")}
        />
        <Button type="submit" size="sm" disabled={createNote.isPending || draft.trim().length === 0}>
          {t("mdNotes.addNote")}
        </Button>
      </form>

      {model.notes.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("mdNotes.empty")}</p>
      ) : (
        <ul className="space-y-3">
          {model.notes.map((note) => (
            <NoteItem
              key={note.id}
              note={note}
              isSaving={patchNote.isPending}
              onSave={(body, onSuccess) => patchNote.mutate({ id: note.id, body }, { onSuccess })}
              onDelete={() => deleteNote.mutate(note.id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
