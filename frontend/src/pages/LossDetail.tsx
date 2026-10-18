/**
 * Loss detail: where one of the briefing's biggest losses comes from. The final multiplication as an equation, the
 * calculation steps, the constants and where they live, and the fact rows the figure is summed from, with a total row
 * that matches the steps, so a manager can check the number end to end.
 */

import { Fragment } from "react";
import { Link, useParams } from "react-router-dom";
import { Badge, Status, num } from "../components/ui";
import { formatIdr, formatIdrCompact } from "../lib/format";
import { useData } from "../lib/store";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

const KIND_LABEL: Record<string, string> = { speed: "Speed loss", scrap: "Scrap", downtime: "Downtime" };
const COLS: Record<string, { label: string; sum?: boolean; digits?: number }> = {
  shift_id: { label: "Shift" },
  machine_id: { label: "Machine" },
  area: { label: "Area" },
  downtime_id: { label: "Downtime ID" },
  total_quantity: { label: "Shots", sum: true },
  output_quantity: { label: "Good", sum: true },
  scrap_quantity: { label: "Scrap", sum: true },
  ideal_cycle_s: { label: "Ideal cycle", digits: 1 },
  actual_cycle_s: { label: "Actual cycle", digits: 2 },
  duration_minutes: { label: "Minutes", sum: true },
  lost_s: { label: "Time lost", sum: true },
};
/** Same for every row: shown once in the filter line instead of repeated down a column. */
const CONSTANT_COLS = ["production_date", "sku"];

function SourceRows({ kind, rows }: { kind: string; rows: Any[] }) {
  const data = kind === "speed" ? rows.map((r) => ({ ...r, lost_s: Math.round((r.actual_cycle_s - r.ideal_cycle_s) * r.total_quantity) })) : rows;
  const keys = data.length ? Object.keys(data[0]).filter((k) => !CONSTANT_COLS.includes(k) && (k !== "machine_id" || kind === "downtime")) : [];
  const maxLost = Math.max(1, ...data.map((r) => r.lost_s ?? r.duration_minutes ?? 0));
  const cell = (k: string, r: Any) => {
    const v = r[k];
    if (typeof v !== "number") return v;
    const c = COLS[k];
    const text = c?.digits ? v.toFixed(c.digits) : num(v);
    if (k === "actual_cycle_s" && r.ideal_cycle_s) {
      const over = v - r.ideal_cycle_s;
      return (
        <>
          {text} s <span className={over > 0 ? "delta bad" : "delta"}>{over > 0 ? "+" : ""}{over.toFixed(1)}</span>
        </>
      );
    }
    if (k === "ideal_cycle_s") return `${text} s`;
    if (k === "lost_s" || k === "duration_minutes") {
      return (
        <span className="cell-bar">
          <span className="cell-bar-fill" style={{ width: `${(100 * v) / maxLost}%` }} />
          <span>{text}{k === "lost_s" ? " s" : ""}</span>
        </span>
      );
    }
    return text;
  };
  if (!data.length) return <p className="muted">No rows.</p>;
  return (
    <div className="table-wrap">
      <table className="trace-table-rows">
        <thead>
          <tr>{keys.map((k) => <th key={k} className={typeof data[0][k] === "number" ? "right" : ""}>{COLS[k]?.label ?? k}</th>)}</tr>
        </thead>
        <tbody>
          {data.map((r, i) => (
            <tr key={i}>{keys.map((k) => <td key={k} className={typeof r[k] === "number" ? "right" : ""}>{cell(k, r)}</td>)}</tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            {keys.map((k, i) => (
              <td key={k} className={typeof data[0][k] === "number" ? "right" : ""}>
                {i === 0 ? "Total" : COLS[k]?.sum ? `${num(data.reduce((a, r) => a + r[k], 0))}${k === "lost_s" ? " s" : ""}` : ""}
              </td>
            ))}
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

export default function LossDetailPage() {
  const { lossId = "" } = useParams();
  const { data: d, error, loading } = useData<Any>(`/api/briefing/losses/${encodeURIComponent(lossId)}`);
  const t = d?.trace;
  return (
    <>
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to="/">Plant Briefing</Link>
        <span aria-hidden="true">›</span>
        <span>Three biggest losses</span>
        <span aria-hidden="true">›</span>
        <span>{d ? `#${d.rank}` : "…"}</span>
      </nav>
      <Status loading={loading} error={error} />
      {d && (
        <>
          <section className="loss-hero">
            <div className="loss-hero-top">
              <div>
                <div className="loss-hero-tags">
                  <Badge tone={d.kind === "downtime" ? "warn" : "bad"}>{KIND_LABEL[d.kind] ?? d.kind}</Badge>
                  <span className="muted small">Rank #{d.rank} · production day {d.day}</span>
                </div>
                <h1>{d.title}</h1>
                <p className="muted">{d.detail}</p>
              </div>
              <div className="loss-hero-figure">
                <div className="loss-hero-idr" title={formatIdr(d.loss_idr)}>{formatIdrCompact(d.loss_idr)}</div>
                {d.share_of_top3_pct != null && (
                  <div className="loss-share">
                    <span className="loss-share-bar"><span style={{ width: `${d.share_of_top3_pct}%` }} /></span>
                    <span className="muted small">{d.share_of_top3_pct}% of the top three</span>
                  </div>
                )}
              </div>
            </div>
            {t.equation && (
              <div className="equation" aria-label="How the loss is multiplied out">
                {t.equation.map((e: Any, i: number) => (
                  <Fragment key={e.label}>
                    {i > 0 && <span className="eq-op">×</span>}
                    <span className="eq-term">
                      <strong>{e.value}</strong>
                      <span>{e.label}</span>
                    </span>
                  </Fragment>
                ))}
                <span className="eq-op">=</span>
                <span className="eq-term result">
                  <strong>{t.steps[t.steps.length - 1].value}</strong>
                  <span>loss</span>
                </span>
              </div>
            )}
            <div className="loss-hero-links">
              {d.machine_id && <Link className="pill-link" to="/mes">{d.machine_id} on the shop floor</Link>}
              <Link className="pill-link" to="/live-board">Live Board</Link>
              <Link className="pill-link" to="/factory-overview">{d.kind === "downtime" ? "Downtime Pareto" : "OEE per machine"}</Link>
            </div>
          </section>

          <div className="loss-grid">
            <section className="card">
              <header className="card-head"><h2>How this number is calculated</h2></header>
              <p className="trace-formula">{t.formula}</p>
              <ol className="stepper">
                {t.steps.map((s: Any, i: number) => (
                  <li key={i} className={i === t.steps.length - 1 ? "final" : ""}>
                    <span className="step-no">{i + 1}</span>
                    <div className="step-text">
                      <strong>{s.label}</strong>
                      <span>{s.expr}</span>
                    </div>
                    <span className="step-value">{s.value}</span>
                  </li>
                ))}
              </ol>
            </section>
            <section className="card">
              <header className="card-head"><h2>Constants used</h2></header>
              <ul className="const-list">
                {t.constants.map((c: Any) => (
                  <li key={c.name}>
                    <div>
                      <div className="const-name">{c.name}</div>
                      {c.link ? <Link to={c.link} className="const-source">{c.source}</Link> : <span className="const-source">{c.source}</span>}
                    </div>
                    <strong>{c.value}</strong>
                  </li>
                ))}
              </ul>
            </section>
          </div>

          <section className="card">
            <header className="card-head">
              <h2>{t.dataset} <span className="count-chip">{t.rows.length}</span></h2>
            </header>
            <div className="trace-scope" aria-label="Records included">
              {t.scope.map((c: Any) => (
                <span key={c.label} className="scope-chip">
                  <span>{c.label}</span>
                  <strong>{c.value}</strong>
                </span>
              ))}
            </div>
            <SourceRows kind={d.kind} rows={t.rows} />
            <p className="source">The total row matches the calculation steps above.</p>
          </section>
        </>
      )}
    </>
  );
}
