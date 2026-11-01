import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useOpenInOrcaSlicer, useOrcaSlicerSession } from "@/api/orcaslicer";
import type { FileOut } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { showOrcaSlicer } from "@/components/orcaslicer/orcaslicerNav";

/** "Open in OrcaSlicer": confirms when it would replace a session on a different file,
 * POSTs, then reveals OrcaSlicer (dock panel on model detail, else the `/slicer` page). */
export function useOrcaSlicerLauncher(slug: string, enabled: boolean): { launch: (file: FileOut) => void; dialog: ReactNode } {
  const { t } = useTranslation();
  const open = useOpenInOrcaSlicer(slug);
  const session = useOrcaSlicerSession({ enabled });
  const [pending, setPending] = useState<FileOut | null>(null);

  function run(file: FileOut) {
    open.mutate(file.id, {
      onSuccess: (out) => {
        toast.success(t("orcaslicer.opening", { name: out.file.name }));
        showOrcaSlicer();
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

  function launch(file: FileOut) {
    const s = session.data;
    if (s?.orcaslicer_running || (s && s.file.id !== file.id)) setPending(file);
    else run(file);
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
        if (pending) run(pending);
        setPending(null);
      }}
    />
  );
  return { launch, dialog };
}
