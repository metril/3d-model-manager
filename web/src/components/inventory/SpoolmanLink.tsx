/** Link/unlink a local spool to a Spoolman spool (Q9), inside the spool detail sheet. */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/api/client";
import { useAuth } from "@/api/auth";
import type { SpoolOut } from "@/api/types";
import { useSpoolmanLink, useSpoolmanRemoteSpools } from "@/api/spoolman";
import { Button } from "@/components/ui/button";

export function SpoolmanLink({ spool }: { spool: SpoolOut }) {
  const { t } = useTranslation();
  const { can } = useAuth();
  const [picking, setPicking] = useState(false);
  const [choice, setChoice] = useState("");
  const remote = useSpoolmanRemoteSpools(picking);
  const { link, unlink } = useSpoolmanLink();

  if (!can("inventory:write")) return null;

  if (spool.spoolman_id != null) {
    return (
      <div className="flex items-center justify-between rounded-md border px-3 py-2 text-sm">
        <span>{t("spoolmanLink.linked", { id: spool.spoolman_id })}</span>
        <Button type="button" size="sm" variant="outline" disabled={unlink.isPending} onClick={() => unlink.mutate(spool.id)}>
          {t("spoolmanLink.unlink")}
        </Button>
        {unlink.isError && (
          <p role="alert" className="text-destructive">
            {unlink.error instanceof ApiError ? unlink.error.detail : t("spoolmanLink.unlinkFailed")}
          </p>
        )}
      </div>
    );
  }

  if (!picking) {
    return (
      <Button type="button" size="sm" variant="outline" onClick={() => setPicking(true)}>
        {t("spoolmanLink.linkTo")}
      </Button>
    );
  }

  return (
    <div className="flex flex-col gap-2 rounded-md border p-3 text-sm">
      {remote.isError ? (
        <p role="alert" className="text-destructive">
          {remote.error instanceof ApiError ? remote.error.detail : t("spoolmanLink.unreachable")}
        </p>
      ) : (
        <select
          aria-label={t("spoolmanLink.spool")}
          className="h-9 rounded-md border border-input bg-background ps-3 text-sm"
          value={choice}
          onChange={(e) => setChoice(e.target.value)}
        >
          <option value="">{remote.isLoading ? t("spoolmanLink.loading") : t("spoolmanLink.pick")}</option>
          {(remote.data ?? []).map((r) => (
            <option key={r.id} value={r.id}>
              #{r.id} {[r.vendor, r.material, r.name].filter(Boolean).join(" ")}
              {r.remaining_weight != null ? ` (${Math.round(r.remaining_weight)} g)` : ""}
            </option>
          ))}
        </select>
      )}
      {link.isError && (
        <p role="alert" className="text-destructive">
          {link.error instanceof ApiError ? link.error.detail : t("spoolmanLink.linkFailed")}
        </p>
      )}
      <div className="flex gap-2">
        <Button
          type="button"
          size="sm"
          disabled={!choice || link.isPending}
          onClick={() =>
            link.mutate(
              { spool_id: spool.id, spoolman_id: Number(choice) },
              { onSuccess: () => setPicking(false) },
            )
          }
        >
          {t("spoolmanLink.link")}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => setPicking(false)}>
          {t("common.cancel")}
        </Button>
      </div>
    </div>
  );
}
