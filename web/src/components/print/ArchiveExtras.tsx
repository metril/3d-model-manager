/** Archive extras shown in RunDetailSheet: notes/tags/override editor, outcome verdict, photos. */
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  archivePhotoUrl,
  useArchiveVerdict,
  useDeleteArchive,
  useDeletePhoto,
  usePatchArchive,
  useUploadPhoto,
} from "@/api/archives";
import { ApiError } from "@/api/client";
import type { ArchiveOut, ArchiveResult } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { formatDateTime } from "@/lib/format";

const RESULTS: ArchiveResult[] = ["completed", "failed", "canceled", "stopped"];
const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";
const errText = (e: unknown, fb: string) => (e instanceof ApiError ? e.detail : fb);

export function VerdictCard({ archive, canWrite }: { archive: ArchiveOut; canWrite: boolean }) {
  const { t } = useTranslation();
  const verdict = useArchiveVerdict();
  if (archive.user_verdict) {
    return (
      <section className="rounded-md border p-3 text-sm">
        <p className="font-medium">
          {archive.user_verdict === "good" ? t("archiveX.verdict.good") : t("archiveX.verdict.bad")}
        </p>
        <p className="text-xs text-muted-foreground">
          {t("archiveX.verdict.via", { source: archive.user_verdict_source ?? "api" })}
          {archive.verdict_at ? ` · ${formatDateTime(archive.verdict_at)}` : ""}
        </p>
      </section>
    );
  }
  if (!archive.confirm_requested || !canWrite) return null;
  const send = (v: "good" | "bad") =>
    verdict.mutate(
      { id: archive.id, verdict: v, source: "card" },
      { onError: (e) => toast.error(errText(e, t("archiveX.verdict.failed"))) },
    );
  return (
    <section className="space-y-2 rounded-md border p-3 text-sm">
      <p className="font-medium">{t("archiveX.verdict.question")}</p>
      <div className="flex gap-2">
        <Button size="sm" onClick={() => send("good")} disabled={verdict.isPending}>
          {t("archiveX.verdict.goodBtn")}
        </Button>
        <Button size="sm" variant="destructive" onClick={() => send("bad")} disabled={verdict.isPending}>
          {t("archiveX.verdict.badBtn")}
        </Button>
      </div>
    </section>
  );
}

export function ArchiveEditor({
  archive,
  canWrite,
  canOverride,
  isAdmin,
  onDeleted,
}: {
  archive: ArchiveOut;
  canWrite: boolean;
  canOverride: boolean;
  isAdmin: boolean;
  onDeleted?: () => void;
}) {
  const { t } = useTranslation();
  const patch = usePatchArchive();
  const del = useDeleteArchive();
  const [notes, setNotes] = useState(archive.notes ?? "");
  const [tags, setTags] = useState<string[]>(archive.tags ?? []);
  const [draft, setDraft] = useState("");
  const [confirmDel, setConfirmDel] = useState(false);
  const fail = (e: unknown) => toast.error(errText(e, t("archiveX.edit.failed")));

  const addTag = () => {
    const v = draft.trim();
    if (v && !tags.includes(v) && tags.length < 20) setTags([...tags, v.slice(0, 40)]);
    setDraft("");
  };

  if (!canWrite) {
    return (archive.notes || archive.tags?.length) ? (
      <section className="space-y-1 text-sm">
        {archive.notes ? <p className="whitespace-pre-wrap">{archive.notes}</p> : null}
        <p className="text-xs text-muted-foreground">{(archive.tags ?? []).join(", ")}</p>
      </section>
    ) : null;
  }

  return (
    <section className="space-y-3">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="archive-notes">{t("archiveX.edit.notes")}</Label>
        <Textarea id="archive-notes" value={notes} maxLength={4000} onChange={(e) => setNotes(e.target.value)} />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="archive-tag">{t("archiveX.edit.tags")}</Label>
        <div className="flex flex-wrap gap-1.5">
          {tags.map((tag) => (
            <span key={tag} className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs">
              {tag}
              <button
                type="button"
                aria-label={t("archiveX.edit.removeTag", { tag })}
                onClick={() => setTags(tags.filter((x) => x !== tag))}
              >
                ×
              </button>
            </span>
          ))}
        </div>
        <input
          id="archive-tag"
          className="h-9 rounded-md border bg-background px-2 text-sm"
          value={draft}
          placeholder={t("archiveX.edit.tagPlaceholder")}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              addTag();
            }
          }}
          onBlur={addTag}
        />
      </div>
      <Button
        size="sm"
        disabled={patch.isPending}
        onClick={() => patch.mutate({ id: archive.id, patch: { notes: notes.trim() || null, tags } }, { onError: fail })}
      >
        {t("archiveX.edit.save")}
      </Button>
      {canOverride ? (
        <div className="flex items-end gap-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="archive-override">{t("archiveX.edit.override")}</Label>
            <select
              id="archive-override"
              className={selectClass}
              value={archive.result_override ?? ""}
              onChange={(e) =>
                patch.mutate(
                  { id: archive.id, patch: { result_override: (e.target.value || null) as ArchiveResult | null } },
                  { onError: fail },
                )
              }
            >
              <option value="">{t("archiveX.edit.overrideNone", { result: archive.recorded_result ?? archive.result })}</option>
              {RESULTS.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </div>
          {archive.result_override ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => patch.mutate({ id: archive.id, patch: { result_override: null } }, { onError: fail })}
            >
              {t("archiveX.edit.reset")}
            </Button>
          ) : null}
        </div>
      ) : null}
      {isAdmin ? (
        <>
          <Button size="sm" variant="destructive" onClick={() => setConfirmDel(true)}>
            {t("archiveX.edit.delete")}
          </Button>
          <ConfirmDialog
            open={confirmDel}
            onOpenChange={setConfirmDel}
            title={t("archiveX.edit.deleteTitle")}
            description={t("archiveX.edit.deleteDesc")}
            confirmLabel={t("archiveX.edit.delete")}
            destructive
            onConfirm={() => del.mutate(archive.id, { onSuccess: () => onDeleted?.(), onError: fail })}
          />
        </>
      ) : null}
    </section>
  );
}

const MAX_PHOTO_BYTES = 15 * 1024 * 1024;
const PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp"];

export function PhotoGallery({ archive, canWrite }: { archive: ArchiveOut; canWrite: boolean }) {
  const { t } = useTranslation();
  const upload = useUploadPhoto(archive.id);
  const del = useDeletePhoto(archive.id);
  const fileRef = useRef<HTMLInputElement>(null);
  const [lightbox, setLightbox] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<string | null>(null);
  const photos = (archive.photos ?? []).filter((p) => p.kind === "photo");
  if (photos.length === 0 && !canWrite) return null;

  const onFile = (file: File | undefined) => {
    if (!file) return;
    if (!PHOTO_TYPES.includes(file.type)) return void toast.error(t("archiveX.photos.badType"));
    if (file.size > MAX_PHOTO_BYTES) return void toast.error(t("archiveX.photos.tooBig"));
    upload.mutate(file, { onError: (e) => toast.error(errText(e, t("archiveX.photos.failed"))) });
  };

  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">{t("archiveX.photos.title")}</h3>
        {canWrite ? (
          <>
            <input
              ref={fileRef}
              type="file"
              hidden
              data-testid="photo-input"
              accept="image/jpeg,image/png,image/webp"
              onChange={(e) => {
                onFile(e.target.files?.[0]);
                e.target.value = "";
              }}
            />
            <Button size="sm" variant="outline" disabled={upload.isPending} onClick={() => fileRef.current?.click()}>
              {t("archiveX.photos.upload")}
            </Button>
          </>
        ) : null}
      </div>
      <div className="grid grid-cols-3 gap-2">
        {photos.map((p) => (
          <div key={p.filename} className="group relative">
            <button type="button" className="block w-full" onClick={() => setLightbox(p.filename)}>
              <img
                src={archivePhotoUrl(archive.id, p.filename)}
                alt={t("archiveX.photos.alt")}
                className="aspect-square w-full rounded-md border object-cover"
              />
            </button>
            {canWrite ? (
              <button
                type="button"
                aria-label={t("archiveX.photos.delete")}
                className="absolute right-1 top-1 rounded bg-background/80 px-1.5 text-xs"
                onClick={() => setToDelete(p.filename)}
              >
                ×
              </button>
            ) : null}
          </div>
        ))}
      </div>
      <Dialog open={lightbox != null} onOpenChange={(o) => !o && setLightbox(null)}>
        <DialogContent className="sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>{t("archiveX.photos.title")}</DialogTitle>
            <DialogDescription className="sr-only">{t("archiveX.photos.alt")}</DialogDescription>
          </DialogHeader>
          {lightbox ? (
            <img src={archivePhotoUrl(archive.id, lightbox)} alt={t("archiveX.photos.alt")} className="max-h-[70vh] w-full object-contain" />
          ) : null}
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={toDelete != null}
        onOpenChange={(o) => !o && setToDelete(null)}
        title={t("archiveX.photos.deleteTitle")}
        confirmLabel={t("archiveX.photos.delete")}
        destructive
        onConfirm={() => toDelete && del.mutate(toDelete)}
      />
    </section>
  );
}
