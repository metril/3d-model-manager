import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useEndStudioSession, useStudioEnabled, useStudioSession } from "@/api/studio";
import { goToModel, isInsecureStudioContext } from "@/components/studio/studioNav";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

function StudioStage() {
  const { t } = useTranslation();
  const [loaded, setLoaded] = useState(false);
  return (
    <div className="relative min-h-0 flex-1">
      {!loaded ? <Skeleton className="absolute inset-0 rounded-none" data-testid="studio-skeleton" /> : null}
      <iframe
        title={t("studio.title")}
        src="/slicer/"
        allow="clipboard-read; clipboard-write; fullscreen"
        className="size-full border-0"
        onLoad={() => setLoaded(true)}
      />
    </div>
  );
}

/** The streamed OrcaSlicer GUI (same-origin `/slicer/`) with a session strip on top.
 * NOTE: moving an iframe in the DOM reloads it, so switching between the dock panel
 * and the `/slicer` page restarts the stream (the Studio process keeps running). */
export function StudioFrame() {
  const { t } = useTranslation();
  const enabled = useStudioEnabled();
  const session = useStudioSession({ enabled });
  const end = useEndStudioSession();
  const s = session.data;

  if (!enabled) {
    return <p className="p-4 text-sm text-muted-foreground">{t("studio.disabled")}</p>;
  }

  return (
    <div className="flex h-full min-h-[24rem] flex-col" data-testid="studio-frame">
      {isInsecureStudioContext() ? (
        <div role="alert" className="border-b border-warning/50 bg-warning/10 px-3 py-1.5 text-xs">
          {t("studio.insecure")}
        </div>
      ) : null}
      <div className="flex items-center gap-2 border-b px-3 py-1.5 text-sm" data-testid="studio-strip">
        {s ? (
          <>
            <span className="min-w-0 flex-1 truncate">
              {t("studio.editing")} <strong>{s.file.name}</strong>
              {" · "}
              <a
                href={`/models/${s.model_slug}`}
                className="text-primary underline-offset-2 hover:underline"
                onClick={(e) => {
                  e.preventDefault();
                  goToModel(s.model_slug);
                }}
              >
                {t("studio.viewModel")}
              </a>
            </span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={end.isPending}
              onClick={() =>
                end.mutate(undefined, {
                  onSuccess: () => toast.success(t("studio.ended")),
                  onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("studio.endError")),
                })
              }
            >
              {t("studio.end")}
            </Button>
          </>
        ) : (
          <span className="flex-1 text-muted-foreground">{t("studio.noSession")}</span>
        )}
      </div>
      {/* Keyed by session so a replaced session remounts (reconnects) the stream. */}
      {session.isPending ? <Skeleton className="min-h-0 flex-1 rounded-none" /> : <StudioStage key={s?.token ?? "none"} />}
    </div>
  );
}
