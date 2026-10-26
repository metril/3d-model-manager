/** Source project + Fusion/CAD file attached to a run (RunDetailSheet). */
import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  archiveSourceUrl,
  useAttachSource,
  useLinkSources,
  useRemoveSource,
  type SourceSlot,
} from "@/api/archives";
import { ApiError } from "@/api/client";
import type { ArchiveOut } from "@/api/types";
import { Button } from "@/components/ui/button";

const MAX_SOURCE_BYTES = 256 * 1024 * 1024;
const errText = (e: unknown, fb: string) => (e instanceof ApiError ? e.detail : fb);

function SourceRow({
  archive,
  slot,
  name,
  canWrite,
}: {
  archive: ArchiveOut;
  slot: SourceSlot;
  name: string | null | undefined;
  canWrite: boolean;
}) {
  const { t } = useTranslation();
  const attach = useAttachSource(archive.id, slot);
  const remove = useRemoveSource(archive.id, slot);
  const fileRef = useRef<HTMLInputElement>(null);
  const linked = slot === "source" && archive.source_linked;
  const onFile = (file: File | undefined) => {
    if (!file) return;
    if (file.size > MAX_SOURCE_BYTES) return void toast.error(t("archiveX.sources.tooBig"));
    attach.mutate(file, { onError: (e) => toast.error(errText(e, t("archiveX.sources.failed"))) });
  };
  return (
    <li className="flex flex-wrap items-center gap-2 text-sm" data-testid={`source-row-${slot}`}>
      <span className="w-36 shrink-0 text-muted-foreground">{t(`archiveX.sources.${slot}`)}</span>
      {name ? (
        <>
          <a
            className="min-w-0 truncate underline-offset-2 hover:underline"
            href={archiveSourceUrl(archive.id, slot)}
            download={name}
          >
            {name}
          </a>
          {linked ? <span className="text-xs text-muted-foreground">{t("archiveX.sources.linked")}</span> : null}
        </>
      ) : (
        <span className="text-muted-foreground">{t("archiveX.sources.none")}</span>
      )}
      {canWrite ? (
        <span className="ml-auto flex gap-1">
          <input
            ref={fileRef}
            type="file"
            hidden
            data-testid={`source-input-${slot}`}
            onChange={(e) => {
              onFile(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
          <Button size="sm" variant="outline" disabled={attach.isPending} onClick={() => fileRef.current?.click()}>
            {name ? t("archiveX.sources.replace") : t("archiveX.sources.attach")}
          </Button>
          {name ? (
            <Button size="sm" variant="ghost" disabled={remove.isPending} onClick={() => remove.mutate()}>
              {t("archiveX.sources.remove")}
            </Button>
          ) : null}
        </span>
      ) : null}
    </li>
  );
}

/** Hidden for read-only viewers when nothing is attached. */
export function SourceFiles({ archive, canWrite }: { archive: ArchiveOut; canWrite: boolean }) {
  const { t } = useTranslation();
  const link = useLinkSources(archive.id);
  const empty = !archive.has_source && !archive.has_f3d;
  if (empty && !canWrite) return null;
  return (
    <section className="space-y-2" data-testid="source-files">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">{t("archiveX.sources.title")}</h3>
        {canWrite && archive.model_id != null && (!archive.has_source || !archive.has_f3d) ? (
          <Button
            size="sm"
            variant="outline"
            disabled={link.isPending}
            onClick={() =>
              link.mutate(undefined, { onError: (e) => toast.error(errText(e, t("archiveX.sources.noModelSource"))) })
            }
          >
            {t("archiveX.sources.linkModel")}
          </Button>
        ) : null}
      </div>
      <ul className="space-y-1.5">
        <SourceRow archive={archive} slot="source" name={archive.has_source ? archive.source_name : null} canWrite={canWrite} />
        <SourceRow archive={archive} slot="f3d" name={archive.has_f3d ? archive.f3d_name : null} canWrite={canWrite} />
      </ul>
    </section>
  );
}
