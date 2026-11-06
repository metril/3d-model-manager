/**
 * "Sync Bambu system profiles" (P6 sidecar profile sync): pulls the slicer
 * sidecar's bundled presets via `useImportProfiles({ fromSidecar })`. By
 * default only the configured printers' models are synced; the checkbox
 * widens it to every Bambu printer. Synced rows are refreshed on re-sync but
 * imported / user-edited profiles are never overwritten (reported as
 * "kept"). Shared by the Profiles page and the Slicer integration card.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCwIcon } from "lucide-react";

import { ApiError } from "@/api/client";
import { useImportProfiles } from "@/api/slicing";
import { Button } from "@/components/ui/button";

export function SyncProfilesButton({ idPrefix = "sync" }: { idPrefix?: string }) {
  const { t } = useTranslation();
  const sync = useImportProfiles();
  const [all, setAll] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  function run() {
    setResult(null);
    sync.mutate(
      { fromSidecar: true, all },
      {
        onSuccess: (r) =>
          setResult(
            t("syncProfiles.result", { created: r.created, updated: r.updated }) +
              (r.skipped ? t("syncProfiles.kept", { n: r.skipped }) : "."),
          ),
      },
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" size="sm" variant="outline" disabled={sync.isPending} onClick={run}>
          <RefreshCwIcon className={sync.isPending ? "size-4 animate-spin" : "size-4"} />
          {sync.isPending ? t("syncProfiles.syncing") : t("syncProfiles.sync")}
        </Button>
        <label htmlFor={`${idPrefix}-all`} className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <input
            id={`${idPrefix}-all`}
            type="checkbox"
            checked={all}
            onChange={(e) => setAll(e.target.checked)}
          />
          {t("syncProfiles.all")}
        </label>
      </div>
      {result ? <p className="text-xs text-muted-foreground">{result}</p> : null}
      {sync.isError ? (
        <p role="alert" className="text-xs text-destructive">
          {sync.error instanceof ApiError
            ? sync.error.detail
            : (sync.error as Error).message || t("syncProfiles.failed")}
        </p>
      ) : null}
    </div>
  );
}
