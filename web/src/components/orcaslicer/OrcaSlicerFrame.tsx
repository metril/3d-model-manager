import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useEndOrcaSlicerSession, useOrcaSlicerEnabled, useOrcaSlicerSession } from "@/api/orcaslicer";
import { goToModel, isInsecureOrcaSlicerContext } from "@/components/orcaslicer/orcaslicerNav";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

function OrcaSlicerStage() {
  const { t } = useTranslation();
  const [loaded, setLoaded] = useState(false);
  return (
    <div className="relative min-h-0 flex-1">
      {!loaded ? <Skeleton className="absolute inset-0 rounded-none" data-testid="orcaslicer-skeleton" /> : null}
      <iframe
        title={t("orcaslicer.title")}
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
 * and the `/slicer` page restarts the stream (the OrcaSlicer process keeps running). */
export function OrcaSlicerFrame() {
  const { t } = useTranslation();
  const enabled = useOrcaSlicerEnabled();
  const session = useOrcaSlicerSession({ enabled });
  const end = useEndOrcaSlicerSession();
  const s = session.data;

  if (!enabled) {
    return <p className="p-4 text-sm text-muted-foreground">{t("orcaslicer.disabled")}</p>;
  }

  return (
    <div className="flex h-full min-h-[24rem] flex-col" data-testid="orcaslicer-frame">
      {isInsecureOrcaSlicerContext() ? (
        <div role="alert" className="border-b border-warning/50 bg-warning/10 px-3 py-1.5 text-xs">
          {t("orcaslicer.insecure")}
        </div>
      ) : null}
      <div className="flex items-center gap-2 border-b px-3 py-1.5 text-sm" data-testid="orcaslicer-strip">
        {s ? (
          <>
            <span className="min-w-0 flex-1 truncate">
              {t("orcaslicer.editing")} <strong>{s.file.name}</strong>
              {" · "}
              <a
                href={`/models/${s.model_slug}`}
                className="text-primary underline-offset-2 hover:underline"
                onClick={(e) => {
                  e.preventDefault();
                  goToModel(s.model_slug);
                }}
              >
                {t("orcaslicer.viewModel")}
              </a>
            </span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={end.isPending}
              onClick={() =>
                end.mutate(undefined, {
                  onSuccess: () => toast.success(t("orcaslicer.ended")),
                  onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("orcaslicer.endError")),
                })
              }
            >
              {t("orcaslicer.end")}
            </Button>
          </>
        ) : (
          <span className="flex-1 text-muted-foreground">{t("orcaslicer.noSession")}</span>
        )}
      </div>
      {/* Keyed by session so a replaced session remounts (reconnects) the stream. */}
      {session.isPending ? <Skeleton className="min-h-0 flex-1 rounded-none" /> : <OrcaSlicerStage key={s?.token ?? "none"} />}
    </div>
  );
}
