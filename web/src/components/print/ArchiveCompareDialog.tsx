/** Side-by-side compare of 2-5 archived runs (GET /archives/compare). */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { archiveThumbnailUrl, useArchiveCompare } from "@/api/archives";
import type { ArchiveOut, CompareOut } from "@/api/types";
import { MAX_COMPARE, type RunSelection } from "@/components/print/useRunSelection";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { humanizeDuration } from "@/lib/format";

export function CompareBar({ selection }: { selection: RunSelection }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (selection.ids.length === 0) return null;
  return (
    <>
      <Button size="sm" disabled={selection.ids.length < 2} onClick={() => setOpen(true)}>
        {t("archiveX.compare.button", { count: selection.ids.length })}
      </Button>
      {selection.ids.length >= MAX_COMPARE ? (
        <span className="text-xs text-muted-foreground">{t("archiveX.compare.max", { max: MAX_COMPARE })}</span>
      ) : null}
      <Button size="sm" variant="ghost" onClick={selection.clear}>
        {t("archiveX.compare.clear")}
      </Button>
      <ArchiveCompareDialog ids={selection.ids} open={open} onOpenChange={setOpen} />
    </>
  );
}

const fmt = (v: unknown): string => (v == null ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));
const grams = (a: CompareOut["filament"][string]): number | null =>
  a && a.length ? a.reduce((s, f) => s + (f.grams ?? 0), 0) : null;

function Compared({ data }: { data: CompareOut }) {
  const { t } = useTranslation();
  const cols = data.archives;
  const name = (a: ArchiveOut) => a.model_name ?? a.subtask_name ?? `#${a.id}`;
  const th = "px-2 py-1.5 text-left align-top font-medium text-muted-foreground";
  return (
    <div className="space-y-6 text-sm">
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr>
              <th className={th} />
              {cols.map((a) => (
                <th key={a.id} className="px-2 py-1.5 text-left align-top">
                  {a.has_thumbnail ? (
                    <img src={archiveThumbnailUrl(a.id)} alt="" className="mb-1 size-20 rounded object-cover" />
                  ) : null}
                  <div className="max-w-40 truncate">{name(a)}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              <th className={th}>{t("archiveX.compare.printer")}</th>
              {cols.map((a) => (
                <td key={a.id} className="px-2 py-1.5">{a.printer_name}</td>
              ))}
            </tr>
            <tr>
              <th className={th}>{t("archiveX.compare.result")}</th>
              {cols.map((a) => (
                <td key={a.id} className="px-2 py-1.5">
                  <Badge variant={a.result === "completed" ? "default" : "destructive"}>{a.result}</Badge>
                </td>
              ))}
            </tr>
            <tr>
              <th className={th}>{t("archiveX.compare.duration")}</th>
              {cols.map((a) => {
                const tm = data.time[String(a.id)];
                return (
                  <td key={a.id} className="px-2 py-1.5 font-mono tabular-nums">
                    {tm?.estimated_s != null ? humanizeDuration(tm.estimated_s) : "—"} /{" "}
                    {tm?.actual_s != null ? humanizeDuration(tm.actual_s) : "—"}
                  </td>
                );
              })}
            </tr>
            <tr>
              <th className={th}>{t("archiveX.compare.filament")}</th>
              {cols.map((a) => {
                const g = grams(data.filament[String(a.id)]);
                return (
                  <td key={a.id} className="px-2 py-1.5 font-mono tabular-nums">
                    {g != null ? `${g} g` : "—"}
                  </td>
                );
              })}
            </tr>
          </tbody>
        </table>
      </div>

      <section>
        <h3 className="mb-2 font-medium">{t("archiveX.compare.settingsDiff")}</h3>
        {data.settings_diff.length === 0 ? (
          <p className="text-muted-foreground">{t("archiveX.compare.noDiff")}</p>
        ) : (
          <table className="w-full">
            <tbody>
              {data.settings_diff.map((row) => (
                <tr key={row.key} data-testid="diff-row" className="bg-amber-500/10">
                  <th className="px-2 py-1.5 text-left font-mono text-xs">{row.key}</th>
                  {cols.map((a) => (
                    <td key={a.id} className="px-2 py-1.5 font-mono text-xs">
                      {fmt(row.values[String(a.id)])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h3 className="mb-2 font-medium">{t("archiveX.compare.correlation")}</h3>
        {data.correlation.length === 0 ? (
          <p className="text-muted-foreground">{t("archiveX.compare.noCorrelation")}</p>
        ) : (
          <table className="w-full">
            <thead>
              <tr>
                <th className={th}>{t("archiveX.compare.setting")}</th>
                <th className={th}>{t("archiveX.compare.value")}</th>
                <th className={th}>{t("archiveX.compare.successRate")}</th>
                <th className={th}>n</th>
              </tr>
            </thead>
            <tbody>
              {data.correlation.map((c, i) => (
                <tr key={i} data-testid="corr-row">
                  <td className="px-2 py-1.5 font-mono text-xs">{c.key}</td>
                  <td className="px-2 py-1.5 font-mono text-xs">{fmt(c.value)}</td>
                  <td className="px-2 py-1.5 font-mono tabular-nums">{Math.round(c.success_rate * 100)}%</td>
                  <td className="px-2 py-1.5 font-mono tabular-nums">{c.n}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

export function ArchiveCompareDialog({
  ids,
  open,
  onOpenChange,
}: {
  ids: number[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const q = useArchiveCompare(ids, open);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle>{t("archiveX.compare.title")}</DialogTitle>
          <DialogDescription>{t("archiveX.compare.desc")}</DialogDescription>
        </DialogHeader>
        {q.isLoading ? (
          <Skeleton className="h-48 w-full" />
        ) : q.isError || !q.data ? (
          <p role="alert" className="text-sm text-destructive">
            {t("archiveX.compare.error")}
          </p>
        ) : (
          <Compared data={q.data} />
        )}
      </DialogContent>
    </Dialog>
  );
}
