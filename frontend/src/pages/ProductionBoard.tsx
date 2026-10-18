/**
 * Module 3, Production Board: what is made, when, and on which machine, for the plans of this session.
 *
 * A lane per machine (or process, shift, day), a bar per work order, drawn on one shared time axis. A bar's position is
 * computed from the window, so a two-hour job is two hours wide wherever it starts. Overlapping bars in a lane are
 * stacked rather than drawn over each other, because the overlap is usually the conflict a dispatcher needs to see.
 * Conflicts are listed above the board and marked on their bars; clicking a bar opens the work order with its
 * dispatch actions.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, Kpi, Source, num } from "../components/ui";
import { useData } from "../lib/store";
import { WorkOrderDialog, dt } from "./WorkOrders";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

const VIEWS: [string, string][] = [["machine", "Machine"], ["process", "Process"], ["shift", "Shift"], ["day", "Day"]];
const ROW_PX = 34;

const BAR_TONE: Record<string, string> = {
  Running: "good",
  "At Risk": "warn",
  Delayed: "bad",
  Maintenance: "info",
  Completed: "done",
  Confirmed: "accent",
  Released: "accent",
  "In Production": "good",
  Scheduled: "plain",
  Draft: "plain",
};

function Timeline({ board, onOpen }: { board: Any; onOpen: (woId: string) => void }) {
  const t0 = Date.parse(board.window_start);
  const t1 = Date.parse(board.window_end);
  const span = Math.max(t1 - t0, 1);
  const hours = span / 3_600_000;
  const step = hours > 24 ? 24 : 2;
  const ticks: { left: number; label: string }[] = [];
  for (let h = 0; h <= hours; h += step) {
    const iso = new Date(t0 + h * 3_600_000 + 7 * 3_600_000).toISOString(); // labels in plant time (WIB)
    ticks.push({ left: (100 * h) / hours, label: hours > 24 ? iso.slice(5, 10) : iso.slice(11, 16) });
  }
  const pos = (v: string) => (100 * (Date.parse(v) - t0)) / span;
  const now = pos(board.now);
  return (
    <div className="board" role="region" aria-label="Production board timeline">
      <div className="board-row board-axis">
        <span className="board-lane-name">{VIEWS.find(([v]) => v === board.view)?.[1]}</span>
        <span className="board-track" style={{ height: 22 }}>
          {ticks.map((t) => (
            <span key={t.left} className="board-tick" style={{ left: `${t.left}%` }}>{t.label}</span>
          ))}
        </span>
      </div>
      {board.lanes.map((ln: Any) => (
        <div key={ln.id} className="board-row">
          <span className="board-lane-name">
            <strong>{ln.name}</strong>
            {ln.subtitle && <span className="small muted">{ln.subtitle}</span>}
          </span>
          <span className="board-track" style={{ height: ln.rows * ROW_PX + 6 }}>
            {ticks.map((t) => <span key={t.left} className="board-grid" style={{ left: `${t.left}%` }} />)}
            {now >= 0 && now <= 100 && <span className="board-now" style={{ left: `${now}%` }} title={`Plant clock ${dt(board.now)}`} />}
            {ln.items.map((it: Any) => {
              const left = Math.max(pos(it.planned_start), 0);
              const width = Math.max(Math.min(pos(it.planned_end), 100) - left, 0.8);
              const blocking = it.conflicts.some((c: Any) => c.blocking);
              const label = it.kind === "MAINTENANCE" ? it.label : `${it.sku} · ${it.plan_number}`;
              const title = [
                it.kind === "MAINTENANCE" ? label : `${it.wo_id} (${it.process})`,
                it.kind === "MAINTENANCE" ? "Approved in the Action Center" : `${it.plan_number} · ${it.board_status} · ${num(it.output_qty)}/${num(it.qty)} pcs`,
                `${dt(it.planned_start)} → ${dt(it.planned_end)}`,
                ...it.conflicts.map((c: Any) => `${c.blocking ? "Blocking" : "Warning"}: ${c.message}`),
              ].join("\n");
              const style = { left: `${left}%`, width: `${width}%`, top: 3 + (it.row ?? 0) * ROW_PX };
              const cls = `board-bar ${BAR_TONE[it.board_status] ?? "plain"} ${blocking ? "clash" : it.conflicts.length ? "warned" : ""} ${it.executing === false ? "tentative" : ""}`;
              return it.kind === "MAINTENANCE" ? (
                <span key={it.id} className={cls} style={style} title={title}>{label}</span>
              ) : (
                <button key={it.id} className={cls} style={style} title={title} aria-label={`${it.wo_id}, ${it.board_status}${blocking ? ", blocking conflict" : ""}`} onClick={() => onOpen(it.wo_id)}>
                  {label}
                  <span className="board-sub">{it.board_status} · {Math.round(it.progress_pct)}%</span>
                </button>
              );
            })}
          </span>
        </div>
      ))}
    </div>
  );
}

export function ProductionBoardPage() {
  const [view, setView] = useState("machine");
  const [date, setDate] = useState("");
  const [days, setDays] = useState(1);
  const [process, setProcess] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const qs = new URLSearchParams(Object.entries({ view, date, days: String(days), process }).filter(([, v]) => v)).toString();
  const { data, error, loading } = useData<Any>(`/api/production-board?${qs}`);
  const blocking = (data?.conflicts ?? []).filter((c: Any) => c.blocking);
  const warnings = (data?.conflicts ?? []).filter((c: Any) => !c.blocking);
  const empty = data && data.summary.work_orders === 0;
  return (
    <>
      <section className="hero">
        <div>
          <p className="hero-tag">Module 3 · MES / Shop Floor</p>
          <h1>Production Board</h1>
          <p className="hero-sub">
            What is made, when and on which machine. A clash that makes a schedule impossible is marked blocking; a delivery, sequence or material risk is a warning.
            Click a bar to dispatch it.
          </p>
          <div className="row">
            <Link to="/work-orders" className="button">Work orders</Link>
          </div>
        </div>
      </section>
      <Card>
        <div className="chips" role="group" aria-label="Board view">
          {VIEWS.map(([v, label]) => (
            <button key={v} className={`chip ${view === v ? "on" : ""}`} aria-pressed={view === v} onClick={() => setView(v)}>{label} view</button>
          ))}
        </div>
        <div className="filters">
          <label>
            Date
            <input type="date" aria-label="Board date" value={date || (data?.window_start ?? "").slice(0, 10)} onChange={(e) => setDate(e.target.value)} />
          </label>
          <label>
            Process
            <select aria-label="Board process" value={process} onChange={(e) => setProcess(e.target.value)}>
              <option value="">All processes</option>
              {["Molding", "Painting", "Assembly"].map((p) => <option key={p}>{p}</option>)}
            </select>
          </label>
          <div className="chips filters-actions" role="group" aria-label="Days shown">
            {[1, 3, 7].map((d) => (
              <button key={d} className={`chip ${days === d ? "on" : ""}`} aria-pressed={days === d} onClick={() => setDays(d)}>{d} day{d > 1 ? "s" : ""}</button>
            ))}
          </div>
        </div>
      </Card>
      {data && (
        <div className="kpis">
          <Kpi label="Work orders on the board" value={num(data.summary.work_orders)} sub={`window from ${dt(data.window_start)}`} />
          <Kpi label="Running" value={num(data.summary.running)} sub={`plant clock ${dt(data.now)}`} tone={data.summary.running ? "good" : undefined} />
          <Kpi label="Late or at risk" value={num(data.summary.late_or_at_risk)} sub="need the dispatcher" tone={data.summary.late_or_at_risk ? "warn" : "good"} />
          <Kpi label="Blocking conflicts" value={num(data.summary.blocking)} sub={`${num(data.summary.warnings)} warning(s)`} tone={data.summary.blocking ? "bad" : "good"} />
        </div>
      )}
      {data && data.conflicts.length > 0 && (
        <Card title="Schedule conflicts" className={blocking.length ? "warn-rail" : ""}>
          <ul className="conflicts">
            {[...blocking, ...warnings].slice(0, 12).map((c: Any, k: number) => (
              <li key={k}>
                <Badge tone={c.blocking ? "bad" : "warn"}>{c.blocking ? "Blocking" : "Warning"}</Badge> {c.message}
              </li>
            ))}
          </ul>
          {data.conflicts.length > 12 && <p className="small muted">and {data.conflicts.length - 12} more.</p>}
        </Card>
      )}
      <Card>
        {error ? (
          <p className="error" role="alert">Could not load the board: {error}</p>
        ) : !data && loading ? (
          <p className="muted">Loading the board…</p>
        ) : data ? (
          <>
            {empty && (
              <div className="empty">
                <strong>Nothing scheduled in this window</strong>
                <p className="muted">Work orders appear once a production plan is scheduled in its wizard. Change the date, widen the range, or open the plans.</p>
                <Link to="/planning" className="button">Production plans</Link>
              </div>
            )}
            <Timeline board={data} onOpen={setOpen} />
            <p className="legend small">
              <span className="board-key good" /> Running <span className="board-key warn" /> At risk <span className="board-key bad" /> Delayed <span className="board-key accent" /> Confirmed / released{" "}
              <span className="board-key plain" /> Scheduled (plan still open) <span className="board-key info" /> Maintenance <span className="board-key clash" /> Blocking conflict
            </p>
          </>
        ) : null}
        <Source>{data?.source ?? "work orders of this session's production plans"}.</Source>
      </Card>
      <WorkOrderDialog woId={open} onClose={() => setOpen(null)} dispatch />
    </>
  );
}

export default ProductionBoardPage;
