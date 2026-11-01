import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useOpenInOrcaSlicer, useOrcaSlicerSession } from "@/api/orcaslicer";
import type { FileOut } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { isInsecureOrcaSlicerContext, openOrcaSlicerWindow, readOrcaSlicerMode, type OrcaSlicerOpenMode } from "@/components/orcaslicer/orcaslicerNav";

/** "Open in OrcaSlicer": confirms when it would replace a session on a different file,
 * opens the OrcaSlicer tab/window synchronously (before any await, so it isn't pop-up blocked),
 * then POSTs; a failed POST just toasts (the tab may be one the user already had open). */
export function useOrcaSlicerLauncher(slug: string, enabled: boolean): { launch: (file: FileOut, mode?: OrcaSlicerOpenMode) => void; dialog: ReactNode } {
  const { t } = useTranslation();
  const open = useOpenInOrcaSlicer(slug);
  const session = useOrcaSlicerSession({ enabled });
  const [pending, setPending] = useState<{ file: FileOut; mode: OrcaSlicerOpenMode } | null>(null);

  function run(file: FileOut, mode: OrcaSlicerOpenMode) {
    if (isInsecureOrcaSlicerContext()) toast.warning(t("orcaslicer.insecure"));
    openOrcaSlicerWindow(mode);
    open.mutate(file.id, {
      onSuccess: (out) => {
        toast.success(t("orcaslicer.opening", { name: out.file.name }));
      },
      onError: (e) => {
        const code = e instanceof ApiError ? e.code : undefined;
        const key =
          code === "orcaslicer_disabled"
            ? "orcaslicer.errDisabled"
            : code === "launcher_unreachable"
              ? "orcaslicer.errLauncher"
              : code === "launcher_busy"
                ? "orcaslicer.errBusy"
                : e instanceof ApiError && e.status === 400
                  ? "orcaslicer.errFormat"
                  : null;
        toast.error(key ? t(key) : e instanceof ApiError ? e.detail : t("orcaslicer.errGeneric"));
      },
    });
  }

  function launch(file: FileOut, mode: OrcaSlicerOpenMode = readOrcaSlicerMode()) {
    const s = session.data;
    if (s?.orcaslicer_running || (s && s.file.id !== file.id)) setPending({ file, mode });
    else run(file, mode);
  }

  const dialog = (
    <ConfirmDialog
      open={pending !== null}
      onOpenChange={(o) => {
        if (!o) setPending(null);
      }}
      title={t("orcaslicer.replaceTitle")}
      description={t("orcaslicer.replaceDesc")}
      confirmLabel={t("orcaslicer.replaceConfirm")}
      onConfirm={() => {
        if (pending) run(pending.file, pending.mode);
        setPending(null);
      }}
    />
  );
  return { launch, dialog };
}
