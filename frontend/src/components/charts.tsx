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
