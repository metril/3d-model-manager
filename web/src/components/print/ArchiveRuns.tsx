/** An archive's runs (reprints of the same file + plate), newest first. */
import { useTranslation } from "react-i18next";

import { useArchiveRuns } from "@/api/archives";
import { Badge } from "@/components/ui/badge";
import { formatCost, formatDateTime, humanizeDuration } from "@/lib/format";

export function RunCountBadge({ count }: { count: number | undefined }) {
  const { t } = useTranslation();
  if (count == null || count < 2) return null;
  return <Badge variant="secondary">{t("archiveX.runs.count", { count })}</Badge>;
}

export function ArchiveRuns({ archiveId }: { archiveId: number }) {
  const { t } = useTranslation();
  const { data } = useArchiveRuns(archiveId);
  if (!data || data.length === 0) return null;
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-medium">{t("archiveX.runs.title")}</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-muted-foreground">
            <tr>
              <th className="pr-2 font-normal">{t("archiveX.runs.date")}</th>
              <th className="pr-2 font-normal">{t("archiveX.runs.printer")}</th>
              <th className="pr-2 font-normal">{t("archiveX.runs.outcome")}</th>
              <th className="pr-2 font-normal">{t("archiveX.runs.duration")}</th>
              <th className="font-normal">{t("archiveX.runs.cost")}</th>
            </tr>
          </thead>
          <tbody>
            {data.map((r) => (
              <tr
                key={r.id}
                data-testid="archive-run"
                data-latest={r.is_latest}
                className={r.is_latest ? "bg-muted/60 font-medium" : undefined}
              >
                <td className="py-1 pr-2">
                  {formatDateTime(r.finished_at ?? r.printed_at)}
                  {r.is_latest ? (
                    <Badge variant="outline" className="ml-1">
                      {t("archiveX.runs.latest")}
                    </Badge>
                  ) : null}
                </td>
                <td className="pr-2">{r.printer_name ?? "--"}</td>
                <td className="pr-2">
                  <Badge variant={r.outcome === "success" ? "default" : "destructive"}>{r.outcome}</Badge>
                </td>
                <td className="pr-2 font-mono tabular-nums">
                  {r.duration_min != null ? humanizeDuration(r.duration_min * 60) : "--"}
                </td>
                <td className="font-mono tabular-nums">{formatCost(r.total_cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
