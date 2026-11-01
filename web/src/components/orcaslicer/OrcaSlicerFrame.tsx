import { useTheme } from "next-themes";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useEndOrcaSlicerSession, useOrcaSlicerEnabled, useOrcaSlicerSession } from "@/api/orcaslicer";
import { goToModel, isInsecureOrcaSlicerContext } from "@/components/orcaslicer/orcaslicerNav";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { SELKIES_THEME_KEY, syncSelkiesTheme, useFollowSelkiesTheme } from "@/lib/selkiesTheme";

const SYNC_RETRIES = 10;
const SYNC_INTERVAL_MS = 500;

function OrcaSlicerStage() {
  const { t } = useTranslation();
  const { resolvedTheme } = useTheme();
  const [loaded, setLoaded] = useState(false);
  const [frameLoads, setFrameLoads] = useState(0);
  const [remount, setRemount] = useState(0);
  const frameRef = useRef<HTMLIFrameElement>(null);
  const remountedFor = useRef<string | null>(null);
  const themeRef = useRef(resolvedTheme);
  themeRef.current = resolvedTheme;
  const loadedTheme = useRef<string | undefined>(undefined);
  useFollowSelkiesTheme();

  // Keep the Selkies dashboard's theme in step with the app: click its own toggle (no
  // reconnect). The dashboard mounts after the frame's load event, so retry briefly; if it
  // never shows a toggle, write its key and remount the frame once so it reads the value.
  useEffect(() => {
    if (frameLoads === 0 || (resolvedTheme !== "dark" && resolvedTheme !== "light")) return;
    const theme = resolvedTheme;
    let tries = 0;
    let timer: ReturnType<typeof setTimeout>;
    const attempt = () => {
      let result: ReturnType<typeof syncSelkiesTheme>;
      try {
        result = syncSelkiesTheme(frameRef.current?.contentDocument, theme);
      } catch {
        result = "missing";
      }
      if (result !== "missing") return;
      if (++tries < SYNC_RETRIES) {
        timer = setTimeout(attempt, SYNC_INTERVAL_MS);
      } else if (loadedTheme.current !== theme && remountedFor.current !== theme) {
        // Only when the theme changed after the frame loaded; at first mount Selkies already
        // read the mirrored value.
        remountedFor.current = theme;
        try {
          localStorage.setItem(SELKIES_THEME_KEY, theme);
        } catch {
          /* storage unavailable */
        }
        setLoaded(false);
        setRemount((n) => n + 1);
      }
    };
    attempt();
    return () => clearTimeout(timer);
  }, [resolvedTheme, frameLoads]);

  return (
    <div className="relative min-h-0 flex-1">
      {!loaded ? <Skeleton className="absolute inset-0 rounded-none" data-testid="orcaslicer-skeleton" /> : null}
      <iframe
        key={remount}
        ref={frameRef}
        title={t("orcaslicer.title")}
        src="/slicer/"
        allow="clipboard-read; clipboard-write; fullscreen"
        className="size-full border-0"
        onLoad={() => {
          setLoaded(true);
          loadedTheme.current = themeRef.current;
          setFrameLoads((n) => n + 1);
        }}
      />
    </div>
  );
}

/** The streamed OrcaSlicer GUI (same-origin `/slicer/`) with a session strip on top. */
export function OrcaSlicerFrame() {
  const { t } = useTranslation();
  const enabled = useOrcaSlicerEnabled();
  const session = useOrcaSlicerSession({ enabled, pollWhileIdle: true });
  const end = useEndOrcaSlicerSession();
  const s = session.data;

  if (!enabled) {
    return <p className="p-4 text-sm text-muted-foreground">{t("orcaslicer.disabled")}</p>;
  }

  return (
    <div className="flex h-full flex-col" data-testid="orcaslicer-frame">
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
      {session.isPending ? (
        <Skeleton className="min-h-0 flex-1 rounded-none" />
      ) : s ? (
        <OrcaSlicerStage key={s.token} />
      ) : (
        <div className="flex min-h-0 flex-1 items-center justify-center text-sm text-muted-foreground" data-testid="orcaslicer-waiting">
          {t("orcaslicer.waiting")}
        </div>
      )}
    </div>
  );
}
