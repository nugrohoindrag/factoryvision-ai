/** Small dependency-free SVG charts: horizontal bars and a line with an optional forecast segment. */

export function Bars({ items, unit = "", max, threshold }: { items: { label: string; value: number; tone?: "good" | "warn" | "bad" }[]; unit?: string; max?: number; threshold?: number }) {
  const top = max ?? Math.max(1, ...items.map((i) => i.value), threshold ?? 0);
  return (
    <div className="bars" role="list">
      {items.map((i) => (
        <div className="bar-row" key={i.label} role="listitem">
          <span className="bar-label">{i.label}</span>
          <span className="bar-track">
            <span className={`bar-fill ${i.tone ?? ""}`} style={{ width: `${Math.min(100, (100 * i.value) / top)}%` }} />
            {threshold != null && <span className="bar-threshold" style={{ left: `${(100 * threshold) / top}%` }} />}
          </span>
          <span className="bar-value">
            {i.value.toLocaleString("en-US", { maximumFractionDigits: 1 })}
            {unit}
          </span>
        </div>
      ))}
    </div>
  );
}

export function Line({ history, forecast = [], height = 260, label }: { history: { x: string; y: number }[]; forecast?: { x: string; y: number }[]; height?: number; label: string }) {
  const all = [...history, ...forecast];
  if (!all.length) return null;
  const w = 1000;
  const pad = 34;
  const ymax = Math.max(...all.map((p) => p.y)) * 1.1 || 1;
  const x = (i: number) => pad + (i * (w - 2 * pad)) / Math.max(1, all.length - 1);
  const y = (v: number) => height - pad - (v / ymax) * (height - 2 * pad);
  const path = (pts: { y: number }[], off: number) => pts.map((p, i) => `${i ? "L" : "M"}${x(i + off).toFixed(1)},${y(p.y).toFixed(1)}`).join(" ");
  const fcStart = Math.max(0, history.length - 1);
  return (
    <svg viewBox={`0 0 ${w} ${height}`} className="line-chart" role="img" aria-label={label}>
      <line x1={pad} y1={height - pad} x2={w - pad} y2={height - pad} className="axis" />
      <path d={path(history, 0)} className="series" />
      {forecast.length > 0 && <path d={path([history[history.length - 1], ...forecast], fcStart)} className="series forecast" />}
      {forecast.length > 0 && <line x1={x(fcStart)} x2={x(fcStart)} y1={pad / 2} y2={height - pad} className="today" />}
      <text x={pad} y={height - 8} className="tick">{all[0].x}</text>
      <text x={w - pad} y={height - 8} className="tick end">{all[all.length - 1].x}</text>
      <text x={pad} y={pad / 2 + 4} className="tick">{Math.round(ymax).toLocaleString("en-US")}</text>
    </svg>
  );
}

/** KPI-card sparkline: the last points of a real daily series, with the final point marked. */
export function Sparkline({ values, label }: { values: number[]; label: string }) {
  if (values.length < 2) return null;
  const w = 88;
  const h = 30;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const pts = values.map((v, i) => [(i * w) / (values.length - 1), h - 3 - ((v - lo) / (hi - lo || 1)) * (h - 8)]);
  const line = pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const [lx, ly] = pts[pts.length - 1];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="spark" role="img" aria-label={label}>
      <polygon points={`0,${h} ${line} ${w},${h}`} className="spark-area" />
      <polyline points={line} className="spark-line" />
      <circle cx={lx} cy={ly} r={2.6} className="spark-dot" />
    </svg>
  );
}

/** Several daily series on one percentage axis, with an optional target line. */
export function TrendChart({ dates, series, target, height = 220, label }: { dates: string[]; series: { name: string; values: number[]; tone: string }[]; target?: number; height?: number; label: string }) {
  if (dates.length < 2) return null;
  const w = 640;
  const pad = { l: 34, r: 12, t: 12, b: 26 };
  const all = series.flatMap((s) => s.values).concat(target ?? []);
  const lo = Math.max(0, Math.floor((Math.min(...all) - 3) / 5) * 5);
  const hi = Math.min(100, Math.ceil((Math.max(...all) + 2) / 5) * 5);
  const x = (i: number) => pad.l + (i * (w - pad.l - pad.r)) / (dates.length - 1);
  const y = (v: number) => pad.t + ((hi - v) / (hi - lo || 1)) * (height - pad.t - pad.b);
  const ticks = [lo, (lo + hi) / 2, hi];
  return (
    <figure className="trend">
      <svg viewBox={`0 0 ${w} ${height}`} className="trend-chart" role="img" aria-label={label}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={w - pad.r} y1={y(t)} y2={y(t)} className="grid" />
            <text x={pad.l - 6} y={y(t) + 4} className="tick end">{Math.round(t)}</text>
          </g>
        ))}
        {target != null && <line x1={pad.l} x2={w - pad.r} y1={y(target)} y2={y(target)} className="target" />}
        {series.map((s) => (
          <path key={s.name} d={s.values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")} className={`trend-line ${s.tone}`} />
        ))}
        <text x={pad.l} y={height - 6} className="tick">{dates[0].slice(5)}</text>
        <text x={w - pad.r} y={height - 6} className="tick end">{dates[dates.length - 1].slice(5)}</text>
      </svg>
      <figcaption className="legend">
        {series.map((s) => (
          <span key={s.name}><i className={s.tone} />{s.name}</span>
        ))}
        {target != null && <span><i className="target" />Target {target}%</span>}
      </figcaption>
    </figure>
  );
}

/** Half-circle gauge for a 0-100 reading; an empty reading draws no arc at all rather than a stub. */
export function Gauge({ value, label, size = 96, stroke = 9 }: { value: number | null; label: string; size?: number; stroke?: number }) {
  const r = (size - stroke) / 2;
  const cx = size / 2;
  const cy = r + stroke / 2;
  const v = Math.max(0, Math.min(100, value ?? 0));
  const arc = (to: number) => {
    const a = Math.PI * (1 - to / 100);
    return `M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${(cx + r * Math.cos(a)).toFixed(2)} ${(cy - r * Math.sin(a)).toFixed(2)}`;
  };
  return (
    <svg viewBox={`0 0 ${size} ${cy + 4}`} width={size} className="gauge" role="img" aria-label={`${label} ${value == null ? "not measured" : `${value}%`}`}>
      <path d={arc(100)} className="gauge-track" strokeWidth={stroke} />
      {v > 0 && <path d={arc(v)} className="gauge-fill" strokeWidth={stroke} />}
      <text x={cx} y={cy - 12} className="gauge-value">{value == null ? "–" : `${value.toFixed(1)}%`}</text>
      <text x={cx} y={cy} className="gauge-label">{label}</text>
    </svg>
  );
}
