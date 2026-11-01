import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useOpenInStudio, useStudioSession } from "@/api/studio";
import type { FileOut } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { showStudio } from "@/components/studio/studioNav";

/** "Open in OrcaSlicer": confirms when it would replace a session on a different file,
 * POSTs, then reveals Studio (dock panel on model detail, else the `/slicer` page). */
export function useStudioLauncher(slug: string, enabled: boolean): { launch: (file: FileOut) => void; dialog: ReactNode } {
  const { t } = useTranslation();
  const open = useOpenInStudio(slug);
  const session = useStudioSession({ enabled });
  const [pending, setPending] = useState<FileOut | null>(null);

  function run(file: FileOut) {
    open.mutate(file.id, {
      onSuccess: (out) => {
        toast.success(t("studio.opening", { name: out.file.name }));
        showStudio();
      },
      onError: (e) => {
        const code = e instanceof ApiError ? e.code : undefined;
        const key =
          code === "studio_disabled"
            ? "studio.errDisabled"
            : code === "launcher_unreachable"
              ? "studio.errLauncher"
              : code === "launcher_busy"
                ? "studio.errBusy"
                : e instanceof ApiError && e.status === 400
                  ? "studio.errFormat"
                  : null;
        toast.error(key ? t(key) : e instanceof ApiError ? e.detail : t("studio.errGeneric"));
      },
    });
  }

  function launch(file: FileOut) {
    const s = session.data;
    if (s?.studio_running || (s && s.file.id !== file.id)) setPending(file);
    else run(file);
  }

  const dialog = (
    <ConfirmDialog
      open={pending !== null}
      onOpenChange={(o) => {
        if (!o) setPending(null);
      }}
      title={t("studio.replaceTitle")}
      description={t("studio.replaceDesc")}
      confirmLabel={t("studio.replaceConfirm")}
      onConfirm={() => {
        if (pending) run(pending);
        setPending(null);
      }}
    />
  );
  return { launch, dialog };
}
