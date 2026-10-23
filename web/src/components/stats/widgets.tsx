import { useTranslation } from "react-i18next";

import type { StatsDashboard } from "@/api/stats";
import { LineChart, type LineSeries } from "@/components/stats/LineChart";
import { StatTile } from "@/components/ui/stat-tile";

const pct = (v: number | null) => (v == null ? "–" : `${Math.round(v * 1000) / 10}`);
const fmtHours = (s: number) => `${Math.round((s / 3600) * 10) / 10} h`;
const SERIES_COLORS = ["var(--viz-1)", "var(--viz-2)", "var(--viz-3)", "var(--viz-4)", "var(--viz-5)", "var(--viz-7)"];
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function Empty() {
  const { t } = useTranslation();
  return <p className="text-sm text-muted-foreground">{t("stats.empty")}</p>;
}

/** Horizontal bars with the value as text beside each (never color-alone). */
function BarList({ rows, color = "var(--viz-1)", format = (v: number) => String(v) }: {
  rows: { key: string; label: string; value: number }[];
  color?: string;
  format?: (v: number) => string;
}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <ul className="space-y-1.5">
      {rows.map((r) => (
        <li key={r.key} className="grid grid-cols-[minmax(0,8rem)_1fr_auto] items-center gap-2 text-xs">
          <span className="truncate" title={r.label}>{r.label}</span>
          <span className="h-2 rounded-r bg-muted">
            <span className="block h-2 rounded-r" style={{ width: `${(r.value / max) * 100}%`, background: color }} />
          </span>
          <span className="tabular-nums">{format(r.value)}</span>
        </li>
      ))}
    </ul>
  );
}

export function QuickStats({ d }: { d: StatsDashboard }) {
  const { t } = useTranslation();
  const hours = d.printers.reduce((a, p) => a + p.hours, 0);
  const grams = d.printers.reduce((a, p) => a + p.filament_g, 0);
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
      <StatTile label={t("stats.total")} value={d.success_rate.total} />
      <StatTile label={t("stats.successShort")} value={pct(d.success_rate.success_rate)} unit="%" />
      <StatTile label={t("stats.hours")} value={Math.round(hours * 10) / 10} unit="h" />
      <StatTile label={t("stats.filamentG")} value={Math.round(grams)} unit="g" />
    </div>
  );
}

export function SuccessRate({ d }: { d: StatsDashboard }) {
  const { t } = useTranslation();
  const s = d.success_rate;
  if (s.total === 0) return <Empty />;
  const parts = [
    { key: "completed", label: t("stats.completed"), n: s.completed, color: "var(--color-state-finished)" },
    { key: "failed", label: t("stats.failed"), n: s.failed, color: "var(--color-state-failed)" },
    { key: "canceled", label: t("stats.canceled"), n: s.canceled + s.stopped, color: "var(--color-state-idle)" },
  ];
  return (
    <div className="space-y-3">
      <StatTile label={t("stats.successShort")} value={pct(s.success_rate)} unit="%" />
      <div className="flex h-2 gap-0.5 overflow-hidden rounded" role="img" aria-label={parts.map((p) => `${p.label} ${p.n}`).join(", ")}>
        {parts.filter((p) => p.n > 0).map((p) => (
          <span key={p.key} style={{ flex: p.n, background: p.color }} />
        ))}
      </div>
      <ul className="space-y-0.5 text-xs">
        {parts.map((p) => (
          <li key={p.key} className="flex items-center gap-2">
            <span aria-hidden className="size-2 rounded-sm" style={{ background: p.color }} />
            {p.label}
            <span className="ml-auto tabular-nums">{p.n}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function TimeAccuracy({ d }: { d: StatsDashboard }) {
  const { t } = useTranslation();
  const a = d.time_accuracy;
  if (a.count === 0) return <Empty />;
  const sign = (a.mean_delta_pct ?? 0) > 0 ? "+" : "";
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <StatTile label={t("stats.meanDelta")} value={`${sign}${Math.round((a.mean_delta_pct ?? 0) * 10) / 10}`} unit="%" />
        <StatTile label={t("stats.meanAbs")} value={Math.round((a.mean_abs_delta_pct ?? 0) * 10) / 10} unit="%" />
      </div>
      <BarList rows={a.distribution.map((b) => ({ key: b.label, label: b.label, value: b.count }))} />
      <p className="text-xs text-muted-foreground">{t("stats.samples", { count: a.count })}</p>
    </div>
  );
}

export function Failures({ d }: { d: StatsDashboard }) {
  const { t } = useTranslation();
  const f = d.failures;
  if (f.total_failed === 0) return <Empty />;
  const groups = [
    { title: t("stats.byErrorCode"), rows: f.by_error_code.map((r) => ({ key: r.key, label: r.key, value: r.count })), fmt: String },
    { title: t("stats.byPrinter"), rows: f.by_printer.filter((r) => r.failed > 0).map((r) => ({ key: r.key, label: r.name ?? r.key, value: r.failure_rate })), fmt: (v: number) => `${pct(v)}%` },
    { title: t("stats.byMaterial"), rows: f.by_material.filter((r) => r.failed > 0).map((r) => ({ key: r.key, label: r.name ?? r.key, value: r.failure_rate })), fmt: (v: number) => `${pct(v)}%` },
  ];
  return (
    <div className="space-y-4">
      {groups.filter((g) => g.rows.length > 0).map((g) => (
        <section key={g.title} className="space-y-1.5">
          <h4 className="label-caps">{g.title}</h4>
          <BarList rows={g.rows.slice(0, 5)} color="var(--viz-2)" format={g.fmt} />
        </section>
      ))}
    </div>
  );
}

export function Heatmap({ d }: { d: StatsDashboard }) {
  const { t } = useTranslation();
  const { counts, max } = d.heatmap;
  if (!max) return <Empty />;
  const cell = 16;
  const gap = 2;
  return (
    <div className="overflow-x-auto">
      <svg
        role="img"
        aria-label={t("stats.w.heatmap")}
        viewBox={`0 0 ${24 * (cell + gap) + 30} ${7 * (cell + gap) + 16}`}
        className="min-w-[32rem] w-full"
      >
        {counts.map((row, wd) => (
          <g key={wd}>
            <text x={0} y={wd * (cell + gap) + cell - 3} className="fill-muted-foreground text-[9px]">{WEEKDAYS[wd]}</text>
            {row.map((n, h) => (
              <rect
                key={h}
                x={30 + h * (cell + gap)}
                y={wd * (cell + gap)}
                width={cell}
                height={cell}
                rx={3}
                fill={n ? "var(--viz-seq)" : "currentColor"}
                fillOpacity={n ? 0.15 + 0.85 * (n / max) : 0.06}
                data-count={n}
              >
                <title>{`${WEEKDAYS[wd]} ${String(h).padStart(2, "0")}:00 UTC - ${t("stats.prints", { count: n })}`}</title>
              </rect>
            ))}
          </g>
        ))}
        {[0, 6, 12, 18].map((h) => (
          <text key={h} x={30 + h * (cell + gap)} y={7 * (cell + gap) + 11} className="fill-muted-foreground text-[9px]">
            {String(h).padStart(2, "0")}
          </text>
        ))}
      </svg>
    </div>
  );
}

export function Records({ d }: { d: StatsDashboard }) {
  const { t } = useTranslation();
  const r = d.records;
  if (!r.longest_print && !r.most_filament && !r.busiest_day) return <Empty />;
  return (
    <dl className="space-y-3 text-sm">
      {r.longest_print && (
        <div>
          <dt className="label-caps">{t("stats.longest")}</dt>
          <dd className="tabular-nums">{fmtHours(r.longest_print.value)} <span className="text-muted-foreground">{r.longest_print.name}</span></dd>
        </div>
      )}
      {r.most_filament && (
        <div>
          <dt className="label-caps">{t("stats.mostFilament")}</dt>
          <dd className="tabular-nums">{Math.round(r.most_filament.value)} g <span className="text-muted-foreground">{r.most_filament.name}</span></dd>
        </div>
      )}
      {r.busiest_day && (
        <div>
          <dt className="label-caps">{t("stats.busiest")}</dt>
          <dd className="tabular-nums">{r.busiest_day.date} <span className="text-muted-foreground">{t("stats.prints", { count: r.busiest_day.prints })}</span></dd>
        </div>
      )}
    </dl>
  );
}

export function Printers({ d }: { d: StatsDashboard }) {
  const { t } = useTranslation();
  if (d.printers.length === 0) return <Empty />;
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="label-caps text-left">
          <th className="py-1 font-medium" />
          <th className="py-1 text-right font-medium">{t("stats.jobs")}</th>
          <th className="py-1 text-right font-medium">{t("stats.successShort")}</th>
          <th className="py-1 text-right font-medium">h</th>
          <th className="py-1 text-right font-medium">g</th>
        </tr>
      </thead>
      <tbody className="tabular-nums">
        {d.printers.map((p) => (
          <tr key={p.printer_id} className="border-t">
            <td className="py-1.5">{p.name}</td>
            <td className="text-right">{p.jobs}</td>
            <td className="text-right">{pct(p.success_rate)}%</td>
            <td className="text-right">{Math.round(p.hours * 10) / 10}</td>
            <td className="text-right">{Math.round(p.filament_g)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Filament({ d }: { d: StatsDashboard }) {
  const { t } = useTranslation();
  const rows = d.filament_trends;
  if (rows.length === 0) return <Empty />;
  const totals = new Map<string, number>();
  for (const r of rows) totals.set(r.material, (totals.get(r.material) ?? 0) + r.grams);
  const ranked = [...totals.entries()].sort((a, b) => b[1] - a[1]).map(([m]) => m);
  const top = ranked.slice(0, 5);
  const weeks = [...new Set(rows.map((r) => Date.parse(r.week)))].sort((a, b) => a - b);
  const sum = (match: (m: string) => boolean, w: number) =>
    rows.filter((r) => Date.parse(r.week) === w && match(r.material)).reduce((a, r) => a + r.grams, 0);
  const series: LineSeries[] = top.map((m, i) => ({
    key: m, label: m, color: SERIES_COLORS[i],
    points: weeks.map((w) => ({ t: w, v: sum((x) => x === m, w) })),
  }));
  if (ranked.length > top.length) {
    series.push({
      key: "other", label: "Other", color: SERIES_COLORS[5],
      points: weeks.map((w) => ({ t: w, v: sum((x) => !top.includes(x), w) })),
    });
  }
  return <LineChart series={series} unit=" g" label={t("stats.w.filament")} formatT={(ts) => new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric" })} />;
}
