/**
 * Module 3, Live Board: the andon view of the shop floor. One card per machine with its state from the digital twin,
 * the work order it is running, and its OEE for the current shift. The twin only advances while this page is open.
 */

import { useState } from "react";
import { Gauge } from "../components/charts";
import { MetricCard, type Tone, num } from "../components/ui";
import { useData } from "../lib/store";
import type { Machine } from "../lib/types";
import { useTwin } from "../twin/useTwin";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

const AREAS: [string, string][] = [["all", "All areas"], ["molding", "Molding"], ["prep", "Paint prep"], ["painting", "Painting"], ["assembly", "Assembly"], ["inspection", "Inspection"]];
/** Below this shift OEE a running machine is flagged, as on the old andon board. */
const LOW_OEE = 50;

function machineTone(m: Machine): Tone {
  if (m.status === "Downtime") return "bad";
  if (m.status === "Maintenance") return "info";
  if (m.status === "Setup" || m.andon === "yellow" || (m.shift.oee_pct != null && m.shift.oee_pct < LOW_OEE)) return "warn";
  return m.status === "Running" ? "good" : "accent";
}

/** The work order a machine is on: the one in production, else the next released one. */
function currentWo(wos: Any[], machineId: string): Any | null {
  const mine = wos.filter((w) => w.machine_id === machineId && !w.children?.length);
  return mine.find((w) => w.status === "In Production") ?? mine.find((w) => w.status === "Released") ?? null;
}

function statusText(m: Machine): string {
  if (m.status === "Downtime") return m.reason_code ? `Downtime · ${m.reason_code.replace(/_/g, " ").toLowerCase()}` : "Downtime";
  if (m.status === "Running" && m.andon === "yellow") return "Speed loss";
  return m.status;
}

function MachineCard({ m, wo }: { m: Machine; wo: Any | null }) {
  const tone = machineTone(m);
  const s = m.shift;
  const hasCycle = m.ideal_cycle_s != null;
  const figures: [string, number | null, boolean][] = [
    ["Availability", s.availability_pct, false],
    ["Performance", s.performance_pct, false],
    ["Quality", s.quality_pct, s.quality_pct != null && s.quality_pct < 98],
  ];
  return (
    <article className={`andon ${tone}`}>
      <header className="andon-head">
        <div className="andon-title">
          <h2>
            {m.machine_name}
            {tone === "good" && <span className="pulse" aria-label="running" />}
          </h2>
          <div className="muted">{m.machine_id} · {m.process ?? m.area}</div>
        </div>
        <span className={`andon-pill ${tone}`}>
          {statusText(m)}
          {m.timer_min ? ` · ${m.timer_min} min` : ""}
        </span>
      </header>
      <div className="andon-body">
        <Gauge value={hasCycle ? s.oee_pct : s.availability_pct} label={hasCycle ? "OEE" : "Avail."} />
        <div className="andon-wo">
          <div className="andon-k">Work order</div>
          {wo ? (
            <>
              <div className="andon-v">{wo.wo_id}</div>
              <div className="andon-qty">
                <span>{num(wo.output_qty)} / {num(wo.qty)} {wo.sku}</span>
                <strong>{wo.progress_pct}%</strong>
              </div>
              <div className="andon-track">
                <span className={wo.progress_pct >= 80 ? "good" : "warn"} style={{ width: `${Math.min(100, wo.progress_pct)}%` }} />
              </div>
            </>
          ) : (
            <>
              <div className="andon-v muted">None released</div>
              <div className="andon-qty">
                <span>{hasCycle ? `${Object.keys(m.mix).join(", ")} · ${num(s.good)} good this shift` : `${num(s.run_min)} of ${num(s.planned_min)} min running`}</span>
              </div>
            </>
          )}
        </div>
      </div>
      <footer className="andon-figures">
        {figures.map(([label, v, bad]) => (
          <div key={label}>
            <div className="andon-k">{label}</div>
            <div className={`andon-fig ${bad ? "bad" : ""}`}>{v == null ? "–" : `${v.toFixed(1)}%`}</div>
          </div>
        ))}
      </footer>
    </article>
  );
}

export default function LiveBoardPage() {
  const { snap, error, paused, setPaused } = useTwin();
  const { data: wos } = useData<Any>("/api/work-orders");
  const [area, setArea] = useState("all");
  const machines = snap?.machines ?? [];
  const shown = machines.filter((m) => area === "all" || m.area === area);
  const running = machines.filter((m) => machineTone(m) === "good").length;
  const down = machines.filter((m) => m.status === "Downtime").length;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Live Shop Floor Board (Andon)</h1>
          <p className="muted">
            Real-time machine status and current-shift OEE from the Vidad digital twin{snap ? ` · plant time ${snap.sim_ts.replace("T", " ")}` : ""}
          </p>
        </div>
        <button className={`stream-pill ${paused ? "off" : ""}`} onClick={() => setPaused(!paused)} title={paused ? "Resume the twin" : "Pause the twin"}>
          <span className="dot" />
          {paused ? "Twin paused" : "Twin stream active"}
        </button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {!snap && !error && <p className="muted">Loading live shop floor telemetry…</p>}
      {snap && (
        <>
          <div className="andon-kpis">
            <MetricCard label="Total monitored machines" icon="factory" value={`${machines.length} machines`} tone="info" sub="Vidad bumper plant" />
            <MetricCard label="Operating normally" icon="check" value={`${running} machines`} tone="good" sub="no stop or speed alarm" />
            <MetricCard label="Active downtime" icon="alert" value={`${down} machines`} tone="bad" delta={{ text: down ? "Requires technician dispatch" : "Optimal condition", good: down === 0 }} />
          </div>
          <div className="chips" role="group" aria-label="Filter area">
            <span className="chips-label">Filter area:</span>
            {AREAS.map(([k, label]) => (
              <button key={k} className={`chip ${area === k ? "on" : ""}`} aria-pressed={area === k} onClick={() => setArea(k)}>
                {label}
              </button>
            ))}
          </div>
          <div className="andon-grid">
            {shown.map((m) => (
              <MachineCard key={m.machine_id} m={m} wo={currentWo(wos?.work_orders ?? [], m.machine_id)} />
            ))}
          </div>
          <p className="source">
            Source: digital twin snapshot (status, cycle, shots, good parts) and the work orders of this session's plans. Shift OEE = A × P × Q since the shift
            started; a machine without a cycle time shows its availability.
          </p>
        </>
      )}
    </>
  );
}
