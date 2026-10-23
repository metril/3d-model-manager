import { useId, useState } from "react";

export interface LineSeries {
  key: string;
  label: string;
  /** CSS var, e.g. `var(--viz-1)`. */
  color: string;
  points: { t: number; v: number | null }[];
}

const W = 560;
const H = 220;
const PAD = { l: 40, r: 12, t: 10, b: 24 };

function niceTicks(min: number, max: number, n = 4): number[] {
  const span = max - min || 1;
  const raw = span / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const start = Math.floor(min / step) * step;
  const out: number[] = [];
  for (let v = start; v <= max + step * 0.5; v += step) out.push(Math.round(v * 100) / 100);
  return out;
}

/** Multi-series line chart: recessive grid, 2px lines, legend (always, >=2
 * series), crosshair + tooltip on hover, and a visually-hidden data table
 * so the values are available without color or pointer. */
export function LineChart({
  series,
  unit = "",
  formatT,
  label,
}: {
  series: LineSeries[];
  unit?: string;
  formatT: (t: number) => string;
  label: string;
}) {
  const id = useId();
  const [hover, setHover] = useState<number | null>(null);
  const all = series.flatMap((s) => s.points.filter((p) => p.v != null) as { t: number; v: number }[]);
  if (all.length === 0) return null;
  const tMin = Math.min(...all.map((p) => p.t));
  const tMax = Math.max(...all.map((p) => p.t));
  const vals = all.map((p) => p.v);
  const ticks = niceTicks(Math.min(0, ...vals), Math.max(...vals));
  const yMin = ticks[0];
  const yMax = ticks[ticks.length - 1] || 1;
  const x = (t: number) => PAD.l + ((t - tMin) / (tMax - tMin || 1)) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + (1 - (v - yMin) / (yMax - yMin || 1)) * (H - PAD.t - PAD.b);
  const times = [...new Set(all.map((p) => p.t))].sort((a, b) => a - b);

  function onMove(e: React.PointerEvent<SVGSVGElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const t = tMin + ((px - PAD.l) / (W - PAD.l - PAD.r)) * (tMax - tMin);
    setHover(times.reduce((best, c) => (Math.abs(c - t) < Math.abs(best - t) ? c : best), times[0]));
  }

  const hoverRows = hover == null ? [] : series.map((s) => ({ s, p: s.points.find((p) => p.t === hover) }));

  return (
    <div className="space-y-2">
      <svg
        role="img"
        aria-labelledby={id}
        viewBox={`0 0 ${W} ${H}`}
        className="w-full touch-none"
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
      >
        <title id={id}>{label}</title>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} stroke="currentColor" strokeOpacity={0.12} />
            <text x={PAD.l - 6} y={y(v)} textAnchor="end" dominantBaseline="middle" className="fill-muted-foreground text-[10px]">
              {v}
            </text>
          </g>
        ))}
        {[tMin, (tMin + tMax) / 2, tMax].map((t, i) => (
          <text
            key={i}
            x={x(t)}
            y={H - 6}
            textAnchor={i === 0 ? "start" : i === 2 ? "end" : "middle"}
            className="fill-muted-foreground text-[10px]"
          >
            {formatT(t)}
          </text>
        ))}
        {series.map((s) => {
          const pts = s.points.filter((p) => p.v != null) as { t: number; v: number }[];
          return (
            <polyline
              key={s.key}
              data-series={s.key}
              points={pts.map((p) => `${x(p.t)},${y(p.v)}`).join(" ")}
              fill="none"
              stroke={s.color}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          );
        })}
        {hover != null && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={PAD.t} y2={H - PAD.b} stroke="currentColor" strokeOpacity={0.35} />
            {hoverRows.map(({ s, p }) =>
              p?.v != null ? (
                <circle key={s.key} cx={x(hover)} cy={y(p.v)} r={4} fill={s.color} stroke="var(--color-background)" strokeWidth={2} />
              ) : null,
            )}
          </g>
        )}
      </svg>
      {hover != null && (
        <div role="status" className="rounded-md border bg-popover px-2 py-1 text-xs tabular-nums">
          <div className="text-muted-foreground">{formatT(hover)}</div>
          {hoverRows.map(({ s, p }) => (
            <div key={s.key}>
              {s.label}: {p?.v == null ? "–" : `${Math.round(p.v * 10) / 10}${unit}`}
            </div>
          ))}
        </div>
      )}
      {series.length > 1 && (
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
          {series.map((s) => (
            <li key={s.key} className="flex items-center gap-1.5">
              <span aria-hidden className="inline-block h-0.5 w-4 rounded" style={{ background: s.color }} />
              {s.label}
            </li>
          ))}
        </ul>
      )}
      <table className="sr-only">
        <caption>{label}</caption>
        <thead>
          <tr>
            <th />
            {series.map((s) => (
              <th key={s.key}>{s.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {times.map((t) => (
            <tr key={t}>
              <th>{formatT(t)}</th>
              {series.map((s) => (
                <td key={s.key}>{s.points.find((p) => p.t === t)?.v ?? ""}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
