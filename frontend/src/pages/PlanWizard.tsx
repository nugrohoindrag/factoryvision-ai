/**
 * The six-step production plan wizard:
 *   1 Demand → 2 Production Plan → 3 Work Order → 4 Scheduling → 5 Resource → 6 Confirmation
 *
 * Three rules shape it. Nothing is retyped: every step starts from what is already recorded (order lines, the
 * forecast snapshot, the capacity assessment, the generated work orders). A step stays locked until its
 * prerequisite exists, and the lock says what is missing; going back is always allowed. And the wizard reopens
 * where the plan got to, which the server works out from the plan's data, not from this browser.
 */

import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Badge, Card, Dialog, Kpi, num, pct } from "../components/ui";
import { del, errorText, get, patch, post } from "../lib/api";
import { useApp, useData } from "../lib/store";
import type { DemandCandidate, MaterialCheck, PlanDetail, PlanLine, PlanWorkOrder } from "../lib/types";
import { ReadinessBadge, qty } from "./Material";
import { AgentBadge, CapacityBadge, FINAL_STATUSES, ForecastLink, PlanStatusBadge } from "./Planning";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

const STEPS = ["Demand", "Production Plan", "Work Order", "Scheduling", "Resource", "Confirmation"];
type Notice = { text: string; tone: "info" | "bad" | "good" };

const dt = (iso: string | null | undefined) => (iso ? iso.slice(0, 16).replace("T", " ") : "–");
const DAY = 86_400_000;

/** Forecast qty of the weeks that overlap [start, end] per SKU, from a snapshot's lines. */
export function forecastInPeriod(snapshot: Any, start: string, end: string): Record<string, number> {
  const s = Date.parse(`${start}T00:00:00Z`);
  const e = Date.parse(`${end}T00:00:00Z`);
  const out: Record<string, number> = {};
  for (const l of snapshot?.lines ?? []) {
    const w = Date.parse(`${l.week_start}T00:00:00Z`);
    if (w <= e && w + 6 * DAY >= s) out[l.sku] = (out[l.sku] ?? 0) + (l.forecast_qty ?? 0);
  }
  return out;
}

/** Work orders still missing a resource: every WO needs a machine; a molding WO also needs its mold. */
export function missingResources(wos: PlanWorkOrder[]): PlanWorkOrder[] {
  return wos.filter((w) => !w.machine_id || (w.process === "Molding" && !w.mold_id));
}

function Empty({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      <p className="muted">{children}</p>
    </div>
  );
}

/* ---------------- Step 1: Demand ---------------- */

function DemandStep({ plan, readOnly, run }: { plan: PlanDetail; readOnly: boolean; run: Run }) {
  const cands = useData<{ candidates: DemandCandidate[] }>(`/api/plans/${plan.plan_id}/demand-candidates`);
  const [qty, setQty] = useState<Record<string, string>>({});
  const byLine = useMemo(() => {
    const m = new Map<string, Any[]>();
    for (const d of plan.demand) m.set(d.line_id, [...(m.get(d.line_id) ?? []), d]);
    return m;
  }, [plan.demand]);
  const inPlan = new Set(plan.demand.map((d) => d.order_id));
  const candidates = (cands.data?.candidates ?? []).filter((c) => c.remaining_qty > 0 && !inPlan.has(c.order_id));
  const add = (c: DemandCandidate) => {
    const raw = qty[c.order_id];
    const body: Record<string, unknown> = { order_id: c.order_id };
    if (raw !== undefined && raw !== "") body.qty = Number(raw);
    run(() => post(`/api/plans/${plan.plan_id}/demand`, body), `${c.order_number} added to the plan.`);
  };
  return (
    <>
      <Card title="Step 1 — Demand">
        <p className="muted small">
          Choose the demand this plan covers. Demand for the same product is combined into one plan line, and each order line keeps its own row, so where the demand came from is never lost.
        </p>
        <h3>Selected demand ({plan.demand.length})</h3>
        {plan.demand.length === 0 ? (
          <p className="muted">No demand selected yet. Add open order lines below.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Customer</th><th>Order</th><th>Product</th><th>Colour</th><th className="right">Demand</th><th>Delivery</th><th /></tr>
              </thead>
              <tbody>
                {plan.lines.map((l) => (
                  <Fragment key={l.line_id}>
                    <tr className="group-row">
                      <td colSpan={4}>
                        <strong>{l.sku}</strong> <span className="muted">{l.product_name}</span>
                      </td>
                      <td className="right"><strong>{num(l.demand_qty)}</strong>{l.forecast_qty > 0 && <div className="muted small">+ {num(l.forecast_qty)} forecast</div>}</td>
                      <td>{l.required_date ?? "–"} <span className="muted small">earliest</span></td>
                      <td />
                    </tr>
                    {(byLine.get(l.line_id) ?? []).map((d) => (
                      <tr key={d.order_id}>
                        <td>{d.customer_name}</td>
                        <td>{d.order_number} <span className="muted small">line {d.line_no}</span></td>
                        <td>{d.sku}</td>
                        <td>{d.color ?? <span className="muted" title="No colour: split across colours by usage share">–</span>}</td>
                        <td className="right">{num(d.qty)}</td>
                        <td>{d.requested_delivery_date ?? "–"}</td>
                        <td>
                          {!readOnly && (
                            <button className="ghost small" onClick={() => run(() => del(`/api/plans/${plan.plan_id}/demand/${encodeURIComponent(d.order_id)}`), `${d.order_number} released from the plan; its quantity is open again.`)}>
                              Release
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Card title={`Open demand (${candidates.length})`}>
        {cands.error ? (
          <p className="error" role="alert">Could not load open demand: {cands.error}</p>
        ) : !cands.data && cands.loading ? (
          <p className="muted">Loading open demand…</p>
        ) : candidates.length === 0 ? (
          <Empty title="No open demand">Every order line is already in a production plan, or there are no new orders.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Customer</th><th>Order</th><th>Product</th><th className="right">Ordered</th><th className="right">Planned</th><th className="right">Remaining</th><th>Delivery</th><th>Qty</th><th /></tr>
              </thead>
              <tbody>
                {candidates.map((c) => (
                  <tr key={c.order_id}>
                    <td>{c.customer_name}</td>
                    <td>{c.order_number} <span className="muted small">line {c.line_no}</span></td>
                    <td>{c.sku}</td>
                    <td className="right">{num(c.ordered_qty)}</td>
                    <td className="right">{num(c.planned_qty)}</td>
                    <td className="right"><strong>{num(c.remaining_qty)}</strong></td>
                    <td>{c.requested_delivery_date ?? "–"}</td>
                    <td>
                      <input
                        type="number"
                        className="qty"
                        aria-label={`Quantity for ${c.order_number}`}
                        min={1}
                        max={c.remaining_qty}
                        value={qty[c.order_id] ?? String(c.remaining_qty)}
                        disabled={readOnly}
                        onChange={(e) => setQty((q) => ({ ...q, [c.order_id]: e.target.value }))}
                      />
                    </td>
                    <td><button className="small" disabled={readOnly} onClick={() => add(c)}>Add</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="muted small">The quantity defaults to what is still unplanned on the order line. The server rejects more than the remainder, cancelled orders and an order line already in this plan.</p>
      </Card>
      <ForecastDemand plan={plan} readOnly={readOnly} run={run} />
    </>
  );
}

/** PP-R30: forecast quantity not yet covered by orders can be planned as extra demand on the SKU's line. */
function ForecastDemand({ plan, readOnly, run }: { plan: PlanDetail; readOnly: boolean; run: Run }) {
  const snap = useData<Any>(plan.forecast_id ? `/api/forecasts/${encodeURIComponent(plan.forecast_id)}` : null);
  const [qty, setQty] = useState<Record<string, string>>({});
  if (!plan.forecast_id) {
    return (
      <Card title="Forecast as demand">
        <p className="muted">This plan does not point to a forecast snapshot. <Link to="/forecast">Generate one in Demand Forecast</Link>.</p>
      </Card>
    );
  }
  const fc = snap.data ? forecastInPeriod(snap.data, plan.period_start, plan.period_end) : {};
  const lineBySku = Object.fromEntries(plan.lines.map((l) => [l.sku, l]));
  const skus = Object.keys(fc).sort();
  return (
    <Card title="Forecast as demand" actions={<span className="small">Snapshot <ForecastLink id={plan.forecast_id} /></span>}>
      {snap.error ? (
        <p className="error" role="alert">Could not load forecast {plan.forecast_id}: {snap.error}</p>
      ) : !snap.data ? (
        <p className="muted">Loading forecast…</p>
      ) : skus.length === 0 ? (
        <p className="muted">The snapshot has no forecast weeks inside this plan's period.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Product</th><th className="right">Forecast in period</th><th className="right">Order demand</th><th className="right">Forecast planned</th><th className="right">Not covered</th><th>Qty</th><th /></tr>
            </thead>
            <tbody>
              {skus.map((sku) => {
                const l = lineBySku[sku];
                const open = Math.max(0, fc[sku] - (l?.demand_qty ?? 0) - (l?.forecast_qty ?? 0));
                return (
                  <tr key={sku}>
                    <td>{sku}</td>
                    <td className="right">{num(fc[sku])}</td>
                    <td className="right">{num(l?.demand_qty ?? 0)}</td>
                    <td className="right">{num(l?.forecast_qty ?? 0)}</td>
                    <td className="right"><strong>{num(open)}</strong></td>
                    <td>
                      <input type="number" className="qty" aria-label={`Forecast quantity for ${sku}`} min={1} value={qty[sku] ?? String(open)} disabled={readOnly || open === 0} onChange={(e) => setQty((q) => ({ ...q, [sku]: e.target.value }))} />
                    </td>
                    <td>
                      <button
                        className="small"
                        disabled={readOnly || open === 0}
                        onClick={() => run(() => post(`/api/plans/${plan.plan_id}/forecast-demand`, { sku, qty: Number(qty[sku] ?? open) }), `Forecast for ${sku} added as demand.`)}
                      >
                        Use as demand
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted small">Forecast quantity is planned on the product's line as forecast demand; the snapshot itself never changes.</p>
    </Card>
  );
}

/* ---------------- Step 2: Production Plan ---------------- */

function LineEditor({ plan, line, readOnly, run }: { plan: PlanDetail; line: PlanLine; readOnly: boolean; run: Run }) {
  const [planned, setPlanned] = useState(String(line.planned_qty));
  const [prio, setPrio] = useState(line.priority);
  useEffect(() => setPlanned(String(line.planned_qty)), [line.planned_qty]);
  useEffect(() => setPrio(line.priority), [line.priority]);
  const n = Number(planned);
  const invalid = planned === "" || !Number.isFinite(n) || n < 0;
  const dirty = n !== line.planned_qty || prio !== line.priority;
  const demand = line.demand_qty + line.forecast_qty;
  const c = line.capacity;
  const save = () => {
    const body: Record<string, number> = {};
    if (n !== line.planned_qty) body.planned_qty = n;
    if (prio !== line.priority) body.priority = prio;
    run(() => patch(`/api/plans/${plan.plan_id}/lines/${line.line_id}`, body), `${line.sku} saved; capacity re-assessed.`);
  };
  return (
    <article className="line-editor" aria-label={`Plan line ${line.sku}`}>
      <header className="row">
        <h3>{line.sku}</h3>
        <span className="muted">{line.product_name}</span>
        <CapacityBadge status={c?.status} />
        {line.required_date && <span className="muted small">deliver by {line.required_date}</span>}
        {line.status === "Released" && <Badge tone="good">Released</Badge>}
      </header>
      <div className="line-grid">
        <div className="field"><span className="muted small">Order demand</span><strong>{num(line.demand_qty)}</strong></div>
        <div className="field"><span className="muted small">Forecast demand</span><strong>{num(line.forecast_qty)}</strong></div>
        <label>
          Planned
          <input type="number" min={0} aria-label={`Planned quantity for ${line.sku}`} value={planned} disabled={readOnly} className={invalid ? "invalid" : ""} onChange={(e) => setPlanned(e.target.value)} />
        </label>
        <label>
          Priority
          <select aria-label={`Priority for ${line.sku}`} value={prio} disabled={readOnly} onChange={(e) => setPrio(Number(e.target.value))}>
            {[1, 2, 3, 4, 5].map((p) => <option key={p} value={p}>{p}{p === 1 ? " (highest)" : p === 5 ? " (lowest)" : ""}</option>)}
          </select>
        </label>
        <button disabled={readOnly || invalid || !dirty} onClick={save}>Save</button>
      </div>
      {invalid && <p className="field-error">Planned quantity cannot be negative.</p>}
      {!invalid && n < demand && <p className="muted small">Planned {num(n)} is {num(demand - n)} below demand {num(demand)}: a decision to produce less than ordered, kept visible.</p>}
      {c ? (
        <>
          <div className="mini-kpis">
            <span title="Σ (available minutes ÷ cycle time) over compatible machines">Total <strong>{num(c.total)}</strong></span>
            <span title={`Total × ${plan.utilization_pct}%`}>Planning <strong>{num(c.planning)}</strong></span>
            <span title="Total − Planning">Buffer <strong>{num(c.buffer)}</strong></span>
            <span title="Demand ÷ Total">Utilization <strong>{pct(c.utilization_pct)}</strong></span>
            <span title="max(Demand − Total, 0)" className={c.gap > 0 ? "neg" : ""}>Gap <strong>{num(c.gap)}</strong></span>
          </div>
          <details>
            <summary className="small">Capacity per machine ({c.contributions.length} compatible{c.uncomputed.length ? `, ${c.uncomputed.length} not computed` : ""})</summary>
            {c.contributions.length > 0 && (
              <table className="compact">
                <thead><tr><th>Machine</th><th className="right">Available minutes</th><th className="right">Cycle time (s)</th><th className="right">Capacity</th></tr></thead>
                <tbody>
                  {c.contributions.map((m) => (
                    <tr key={m.machine_id}><td>{m.machine_id}</td><td className="right">{num(m.available_minutes)}</td><td className="right">{m.cycle_s}</td><td className="right">{num(m.capacity)}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
            {c.uncomputed.length > 0 && (
              <div className="notice-box warn small">
                Not computed, and not counted as zero capacity:
                <ul>{c.uncomputed.map((u) => <li key={u.machine_id}><strong>{u.machine_id}</strong>: {u.reason}</li>)}</ul>
              </div>
            )}
          </details>
        </>
      ) : (
        <p className="muted small">Capacity is assessed when the line is saved.</p>
      )}
    </article>
  );
}

function PlanStep({ plan, readOnly, run }: { plan: PlanDetail; readOnly: boolean; run: Run }) {
  return (
    <Card title="Step 2 — Production Plan">
      <p className="muted small">
        Set the planned quantity and priority per line. Demand and planned are kept apart so a decision to produce less than was ordered stays visible. The server recomputes capacity on every
        save, at the plan's {plan.utilization_pct}% planning utilization.
      </p>
      {plan.lines.length === 0 ? <Empty title="No plan lines">Add demand in Step 1 first.</Empty> : plan.lines.map((l) => <LineEditor key={l.line_id} plan={plan} line={l} readOnly={readOnly} run={run} />)}
      <p className="muted small">Within Plan: demand ≤ planning capacity · Additional Demand: still fits in the buffer · Capacity Up Required: demand exceeds total capacity.</p>
    </Card>
  );
}

/* ---------------- Step 3: Work Order ---------------- */

const WO_TONE: Record<string, "neutral" | "good" | "warn" | "info"> = { Draft: "neutral", Confirmed: "info", Released: "info", "In Production": "warn", Completed: "good" };

function sortWos(wos: PlanWorkOrder[], lines: PlanLine[]) {
  const order = new Map(lines.map((l, i) => [l.line_id, i]));
  return [...wos].sort((a, b) => (order.get(a.line_id) ?? 0) - (order.get(b.line_id) ?? 0) || (a.sequence ?? 0) - (b.sequence ?? 0));
}

function WorkOrderStep({ plan, readOnly, run, result }: { plan: PlanDetail; readOnly: boolean; run: Run; result: Any }) {
  const wos = sortWos(plan.work_orders, plan.lines);
  return (
    <Card
      title="Step 3 — Work Order"
      actions={
        <button className="primary" disabled={readOnly || plan.lines.length === 0} onClick={() => run(() => post(`/api/plans/${plan.plan_id}/work-orders`), null, "generate")}>
          Generate work orders
        </button>
      }
    >
      <p className="muted small">One work order per routing process per plan line (molding → painting → assembly), each for the planned quantity, chained by sequence and predecessor. Generating again never creates duplicates.</p>
      {result && (
        <div className="notice-box good" role="status">
          {result.created.length} work order{result.created.length === 1 ? "" : "s"} created, {result.existed.length} already existed.
          {result.created.length > 0 && <div className="small">Created: {result.created.map((w: Any) => (typeof w === "string" ? w : w.wo_id)).join(", ")}</div>}
          {result.existed.length > 0 && <div className="small">Already existed: {result.existed.map((w: Any) => (typeof w === "string" ? w : w.wo_id)).join(", ")}</div>}
        </div>
      )}
      {wos.length === 0 ? (
        <Empty title="No work orders yet">Generate to create one work order per process in each product's routing.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Work order</th><th>Product</th><th>Process</th><th className="right">Seq</th><th className="right">Qty</th><th>Predecessor</th><th>Status</th></tr></thead>
            <tbody>
              {wos.map((w) => (
                <tr key={w.wo_id}>
                  <td><strong>{w.wo_id}</strong></td>
                  <td>{w.sku}</td>
                  <td>{w.process}</td>
                  <td className="right">{w.sequence ?? "–"}</td>
                  <td className="right">{num(w.qty)}</td>
                  <td>{w.predecessor_wo_id ?? "–"}</td>
                  <td><Badge tone={WO_TONE[w.status] ?? "neutral"}>{w.status}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/* ---------------- Step 4: Scheduling ---------------- */

const SKU_TONES = 6;
const toneOf = (sku: string) => [...sku].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) >>> 0, 7) % SKU_TONES;

/** One row per machine, a bar per scheduled work order across the plan period. */
export function Gantt({ wos, start, end }: { wos: PlanWorkOrder[]; start: string; end: string }) {
  const placed = wos.filter((w) => w.machine_id && w.planned_start && w.planned_end);
  if (!placed.length) return null;
  const t0 = Math.min(Date.parse(`${start}T00:00:00Z`), ...placed.map((w) => Date.parse(w.planned_start!)));
  const t1 = Math.max(Date.parse(`${end}T23:59:59Z`), ...placed.map((w) => Date.parse(w.planned_end!)));
  const span = Math.max(1, t1 - t0);
  const machines = [...new Set(placed.map((w) => w.machine_id!))].sort();
  const days = Math.round(span / DAY);
  const ticks = Array.from({ length: Math.min(days, 8) + 1 }, (_, i) => t0 + (i * span) / Math.min(days || 1, 8));
  return (
    <div className="gantt" role="img" aria-label={`Schedule of ${placed.length} work orders on ${machines.length} machines`}>
      <div className="gantt-row gantt-axis">
        <span />
        <span className="gantt-track">
          {ticks.map((t) => <span key={t} className="gantt-tick" style={{ left: `${(100 * (t - t0)) / span}%` }}>{new Date(t).toISOString().slice(5, 10)}</span>)}
        </span>
      </div>
      {machines.map((m) => (
        <div key={m} className="gantt-row">
          <span className="gantt-label">{m}</span>
          <span className="gantt-track">
            {placed.filter((w) => w.machine_id === m).map((w) => {
              const a = Date.parse(w.planned_start!);
              const b = Date.parse(w.planned_end!);
              return (
                <span
                  key={w.wo_id}
                  className={`gantt-bar t${toneOf(w.sku)}`}
                  style={{ left: `${(100 * (a - t0)) / span}%`, width: `${Math.max(0.6, (100 * (b - a)) / span)}%` }}
                  title={`${w.wo_id} · ${w.process} · ${num(w.qty)} pcs · ${dt(w.planned_start)} → ${dt(w.planned_end)}${w.shift ? ` · shift ${w.shift}` : ""}`}
                >
                  {w.sku}
                </span>
              );
            })}
          </span>
        </div>
      ))}
    </div>
  );
}

function ScheduleStep({ plan, readOnly, run, result }: { plan: PlanDetail; readOnly: boolean; run: Run; result: Any }) {
  const wos = sortWos(plan.work_orders, plan.lines);
  return (
    <Card
      title="Step 4 — Scheduling"
      actions={
        <button className="primary" disabled={readOnly || wos.length === 0} onClick={() => run(() => post(`/api/plans/${plan.plan_id}/schedule`), null, "schedule")}>
          Run scheduler (CP-SAT)
        </button>
      }
    >
      <p className="muted small">OR-Tools CP-SAT sets the start, finish, shift and sequence of every work order, respecting which molds fit which machines and the changeover between molds.</p>
      {result && (
        <div className={`notice-box ${String(result.status).toUpperCase() === "INFEASIBLE" ? "bad" : "good"}`} role="status">
          Solver status <strong>{result.status}</strong> in {Number(result.solve_seconds ?? 0).toFixed(2)} s.
        </div>
      )}
      {wos.length === 0 ? (
        <Empty title="No work orders to schedule">Go back to Step 3 and generate work orders first.</Empty>
      ) : (
        <>
          <Gantt wos={wos} start={plan.period_start} end={plan.period_end} />
          <div className="table-wrap">
            <table>
              <thead><tr><th>Work order</th><th>Product</th><th>Process</th><th className="right">Seq</th><th>Machine</th><th>Planned start</th><th>Planned end</th><th>Shift</th></tr></thead>
              <tbody>
                {wos.map((w) => (
                  <tr key={w.wo_id}>
                    <td><strong>{w.wo_id}</strong></td>
                    <td>{w.sku}</td>
                    <td>{w.process}</td>
                    <td className="right">{w.sequence ?? "–"}</td>
                    <td>{w.machine_id ?? <span className="muted">not set</span>}</td>
                    <td>{dt(w.planned_start)}</td>
                    <td>{dt(w.planned_end)}</td>
                    <td>{w.shift ?? <span className="muted">not set</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}

/* ---------------- Step 5: Resource ---------------- */

/** Compatible choices: from the backend when it lists them, else from the plan's own capacity assessment. */
function choices(w: PlanWorkOrder, plan: PlanDetail): { machines: string[]; molds: string[] } {
  const line = plan.lines.find((l) => l.line_id === w.line_id);
  const fromCapacity = w.process === "Molding" ? (line?.capacity?.contributions ?? []).map((c) => c.machine_id) : [];
  const sameProcess = plan.work_orders.filter((x) => x.process === w.process && x.machine_id).map((x) => x.machine_id!);
  const machines = w.compatible_machines ?? (fromCapacity.length ? fromCapacity : sameProcess);
  const molds = w.compatible_molds ?? plan.work_orders.filter((x) => x.sku === w.sku && x.mold_id).map((x) => x.mold_id!);
  const uniq = (xs: (string | null)[]) => [...new Set(xs.filter((x): x is string => !!x))].sort();
  return { machines: uniq([...machines, w.machine_id]), molds: uniq([...molds, w.mold_id]) };
}

function ResourceStep({ plan, readOnly, run }: { plan: PlanDetail; readOnly: boolean; run: Run }) {
  const wos = sortWos(plan.work_orders, plan.lines);
  const missing = missingResources(wos);
  const unconfirmed = wos.filter((w) => !w.confirmed);
  const set = (w: PlanWorkOrder, field: "machine_id" | "mold_id", value: string) =>
    run(() => patch(`/api/plans/${plan.plan_id}/work-orders/${encodeURIComponent(w.wo_id)}`, { [field]: value }), `${w.wo_id}: ${field === "machine_id" ? "machine" : "mold"} set to ${value}.`);
  return (
    <Card
      title="Step 5 — Resource"
      actions={
        <button className="primary" disabled={readOnly || wos.length === 0 || missing.length > 0 || unconfirmed.length === 0} onClick={() => run(() => post(`/api/plans/${plan.plan_id}/work-orders/confirm`), "Work orders confirmed.")}>
          Confirm work orders{unconfirmed.length ? ` (${unconfirmed.length})` : ""}
        </button>
      }
    >
      <p className="muted small">Only machines and molds compatible with the product are offered; the server rejects any combination that does not fit.</p>
      {wos.length === 0 ? (
        <Empty title="No work orders yet">Resources are assigned after work orders are generated and scheduled.</Empty>
      ) : (
        <>
          {missing.length > 0 && (
            <div className="notice-box warn" role="status">
              {missing.length} work order{missing.length === 1 ? " has" : "s have"} no machine and/or mold yet. Confirmation stays locked until every one is filled.
            </div>
          )}
          <div className="table-wrap">
            <table>
              <thead><tr><th>Work order</th><th>Product</th><th>Process</th><th>Machine</th><th>Mold</th><th>Confirmed</th></tr></thead>
              <tbody>
                {wos.map((w) => {
                  const c = choices(w, plan);
                  return (
                    <tr key={w.wo_id}>
                      <td><strong>{w.wo_id}</strong></td>
                      <td>{w.sku}</td>
                      <td>{w.process}</td>
                      <td>
                        <select aria-label={`Machine for ${w.wo_id}`} value={w.machine_id ?? ""} disabled={readOnly || w.confirmed} onChange={(e) => e.target.value && set(w, "machine_id", e.target.value)}>
                          {!w.machine_id && <option value="">Not set</option>}
                          {c.machines.map((m) => <option key={m}>{m}</option>)}
                        </select>
                      </td>
                      <td>
                        {w.process === "Molding" || w.mold_id || c.molds.length ? (
                          <select aria-label={`Mold for ${w.wo_id}`} value={w.mold_id ?? ""} disabled={readOnly || w.confirmed} onChange={(e) => e.target.value && set(w, "mold_id", e.target.value)}>
                            {!w.mold_id && <option value="">Not set</option>}
                            {c.molds.map((m) => <option key={m}>{m}</option>)}
                          </select>
                        ) : (
                          <span className="muted small">no mold for {w.process.toLowerCase()}</span>
                        )}
                      </td>
                      <td>{w.confirmed ? <Badge tone="good">Confirmed</Badge> : <Badge>{w.status}</Badge>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}

/* ---------------- Step 6: Confirmation ---------------- */

/** MR-R26: the plan's material readiness before it is committed. A shortage warns; it does not block Confirm. */
export function MaterialReadinessPanel({ planId, readOnly }: { planId: string; readOnly: boolean }) {
  const [check, setCheck] = useState<MaterialCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const path = `/api/materials/readiness/plans/${encodeURIComponent(planId)}`;
  const recheck = async () => {
    setBusy(true);
    setErr(null);
    try {
      setCheck(await post<MaterialCheck>(path));
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    let alive = true;
    setBusy(true);
    // The saved check shows at once; a plan that can still change is then checked against stock now.
    get<MaterialCheck>(path)
      .then((c) => alive && setCheck(c))
      .catch((e) => alive && setErr(errorText(e)))
      .then(() => (alive && !readOnly ? post<MaterialCheck>(path) : null))
      .then((c) => alive && c && (setCheck(c), setErr(null)))
      .catch((e) => alive && setErr(errorText(e)))
      .finally(() => alive && setBusy(false));
    return () => {
      alive = false;
    };
  }, [path, readOnly]);
  const status = check?.status ?? "NOT_CHECKED";
  const shortages = check?.shortages ?? (check?.materials ?? []).filter((m) => m.shortage_qty > 0);
  return (
    <div className="card" aria-label="Material readiness">
      <header className="card-head">
        <h2>Material readiness</h2>
        <div className="card-actions">
          <button className="small" onClick={recheck} disabled={busy}>{busy ? "Checking…" : "Check again"}</button>
        </div>
      </header>
      <p className="small">
        <ReadinessBadge status={status} /> {check?.readiness_pct != null && <><strong>{pct(check.readiness_pct, 0)}</strong> of materials ready</>}
        {check?.checked_at && <span className="muted"> · checked {dt(check.checked_at)}</span>}{" "}
        <Link to={`/material?tab=readiness&plan=${encodeURIComponent(planId)}`}>Open in Material Supply</Link>
      </p>
      {err && <p className="error small">Material readiness could not be checked: {err}</p>}
      {status === "SHORTAGE" && (
        <div className="notice-box warn" role="status">
          No material of this plan is fully covered. You can still confirm; the shortage stays visible in Material Supply, where an MRP run can send the purchase to Purchasing.
        </div>
      )}
      {status === "PARTIAL" && (
        <div className="notice-box warn" role="status">
          {shortages.length} material{shortages.length === 1 ? " is" : "s are"} short for this plan. Confirming is allowed; plan the purchase in Material Supply.
        </div>
      )}
      {status === "READY" && <div className="notice-box good">All materials are available by their need dates.</div>}
      {shortages.length > 0 && (
        <div className="table-wrap">
          <table aria-label="Material shortages">
            <thead><tr><th>Material</th><th>Colour</th><th className="right">Required</th><th className="right">Available</th><th className="right">Shortage</th><th>Need date</th></tr></thead>
            <tbody>
              {shortages.map((m) => (
                <tr key={`${m.material_id}-${m.color ?? ""}`}>
                  <td><strong>{m.material_id}</strong> <span className="muted small">{m.material_name}</span>{m.note && <div className="muted small">{m.note}</div>}</td>
                  <td>{m.color ?? "–"}</td>
                  <td className="right">{qty(m.required_qty)} {m.unit}</td>
                  <td className="right">{qty(m.available_qty)}</td>
                  <td className="right neg">{qty(m.shortage_qty)}</td>
                  <td>{m.required_date ?? "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ConfirmStep({ plan, readOnly, run }: { plan: PlanDetail; readOnly: boolean; run: Run }) {
  const s = plan.summary;
  const capUp = plan.lines.filter((l) => l.capacity?.status === "Capacity Up Required");
  const unconfirmed = plan.work_orders.filter((w) => !w.confirmed);
  return (
    <Card title="Step 6 — Confirmation">
      <p className="muted small">The full plan at a glance. Confirming makes it a commitment: the lines are released and the linked customer orders move to Planned.</p>
      <div className="kpis">
        <Kpi label="Plan lines" value={num(s.lines)} />
        <Kpi label="Total planned" value={num(s.total_planned)} />
        <Kpi label="Work orders" value={num(s.work_orders)} />
        <Kpi label="Customer orders" value={num(s.customer_orders)} />
      </div>
      {!readOnly && (
        <div className="stack">
          {s.lines === 0 && <div className="notice-box bad">The plan has no lines. Add demand in Step 1.</div>}
          {capUp.length > 0 && (
            <div className="notice-box bad">
              {capUp.length} plan line{capUp.length === 1 ? " is" : "s are"} Capacity Up Required ({capUp.map((l) => l.sku).join(", ")}). Lower the planned quantity or request more capacity before
              confirming.
            </div>
          )}
          {s.work_orders === 0 && <div className="notice-box bad">No work orders yet. Generate them in Step 3.</div>}
          {s.work_orders > 0 && unconfirmed.length > 0 && <div className="notice-box warn">{unconfirmed.length} work order{unconfirmed.length === 1 ? " is" : "s are"} not confirmed yet. Confirm them in Step 5.</div>}
        </div>
      )}
      <MaterialReadinessPanel planId={plan.plan_id} readOnly={FINAL_STATUSES.includes(plan.status)} />
      <div className="dialog-foot">
        <button className="primary big" disabled={readOnly} onClick={() => run(() => post(`/api/plans/${plan.plan_id}/confirm`), `${plan.plan_number} confirmed.`)}>
          Confirm production plan
        </button>
      </div>
      <p className="muted small">The server checks every condition again and says why if it refuses.</p>
    </Card>
  );
}

/** PP-R73: a reason of at least 3 characters; the server refuses when work orders are already running. */
function CancelDialog({ open, plan, onClose, run }: { open: boolean; plan: PlanDetail; onClose: () => void; run: Run }) {
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const short = reason.trim().length < 3;
  const close = () => {
    setReason("");
    setErr(null);
    onClose();
  };
  const submit = async () => {
    const ok = await run(() => post(`/api/plans/${plan.plan_id}/cancel`, { reason: reason.trim() }), `${plan.plan_number} cancelled; its demand is open again.`, undefined, setErr);
    if (ok) close();
  };
  return (
    <Dialog open={open} title={`Cancel ${plan.plan_number}`} onClose={close}>
      <p className="muted small">Cancelling returns every demand quantity to its order line and recomputes the customer orders' status. A plan with work orders in production or completed cannot be cancelled.</p>
      <div className="form-grid">
        <label>
          Reason
          <textarea aria-label="Reason" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />
          {short && reason.length > 0 && <span className="field-error">Give a reason of at least 3 characters.</span>}
        </label>
      </div>
      {err && <p className="error" role="alert">{err}</p>}
      <div className="dialog-foot">
        <button className="ghost" onClick={close}>Keep the plan</button>
        <button className="primary" disabled={short} onClick={submit}>Cancel plan</button>
      </div>
    </Dialog>
  );
}

/* ---------------- Frame ---------------- */

type Run = (fn: () => Promise<unknown>, success: string | null, keep?: "generate" | "schedule", onError?: (msg: string) => void) => Promise<boolean>;

export default function PlanWizard() {
  const { planId = "" } = useParams();
  const { bump } = useApp();
  const { data: plan, error, loading } = useData<PlanDetail>(`/api/plans/${encodeURIComponent(planId)}`);
  const [step, setStep] = useState(1);
  const [restored, setRestored] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<{ generate?: Any; schedule?: Any }>({});
  const [cancelling, setCancelling] = useState(false);

  // The server's resume point, once: after that the planner is navigating and a refetch must not yank them back.
  useEffect(() => {
    if (plan && !restored) {
      setStep(Math.min(6, Math.max(1, plan.readiness?.resume_step ?? plan.wizard_step ?? 1)));
      setRestored(true);
    }
  }, [plan, restored]);

  const run: Run = async (fn, success, keep, onError) => {
    setBusy(true);
    setNotice(null);
    try {
      const r = await fn();
      if (keep) setResults((x) => ({ ...x, [keep]: r }));
      if (success) setNotice({ text: success, tone: "good" });
      bump();
      return true;
    } catch (e) {
      const msg = errorText(e);
      if (onError) onError(msg);
      else setNotice({ text: msg, tone: "bad" });
      return false;
    } finally {
      setBusy(false);
    }
  };

  if (!plan || !restored) {
    return (
      <>
        <Link to="/planning" className="small">← Production plans</Link>
        {error ? (
          <div className="notice-box bad" role="alert">
            <strong>The production plan could not be loaded.</strong> It may have been removed by a session reset, or the API is unreachable. {error}{" "}
            <button className="small" onClick={bump}>Retry</button>
          </div>
        ) : (
          <p className="muted">{loading ? "Loading production plan…" : ""}</p>
        )}
      </>
    );
  }

  const readOnly = FINAL_STATUSES.includes(plan.status);
  const steps = plan.readiness?.steps ?? STEPS.map((name, i) => ({ step: i + 1, name, ready: true, blocked_by: null }));
  const info = (n: number) => steps.find((s) => s.step === n);
  const goTo = async (target: number) => {
    if (target < 1 || target > 6) return;
    const s = info(target);
    if (target > step && s && !s.ready) {
      setNotice({ text: s.blocked_by ?? `Step ${target} is not available yet.`, tone: "bad" });
      return;
    }
    setNotice(null);
    setStep(target);
    // Only forward progress is stored: the plan's step is how far it got, not where this browser is looking.
    if (!readOnly && target > (plan.wizard_step ?? 1)) {
      try {
        await post(`/api/plans/${plan.plan_id}/step`, { step: target });
        bump();
      } catch (e) {
        setNotice({ text: errorText(e), tone: "bad" });
      }
    }
  };
  const next = info(step + 1);
  const props = { plan, readOnly: readOnly || busy, run };

  return (
    <>
      <Link to="/planning" className="small">← Production plans</Link>
      <div className="page-head">
        <div>
          <h1>
            {plan.plan_number} {plan.origin === "agent" && <AgentBadge />}
          </h1>
          <p className="muted">
            Period {plan.period_start} → {plan.period_end} · <PlanStatusBadge status={plan.status} /> · planning utilization {plan.utilization_pct}% · forecast <ForecastLink id={plan.forecast_id} /> · created by{" "}
            {plan.created_by} {plan.created_at?.slice(0, 10)}
          </p>
        </div>
        {!readOnly && <button className="ghost" onClick={() => setCancelling(true)}>Cancel plan…</button>}
      </div>
      {readOnly && (
        <div className="notice-box info" role="status">
          This plan is {plan.status} and can no longer be changed.{" "}
          {plan.status === "Cancelled"
            ? `Cancelled by ${plan.cancelled_by ?? "–"} at ${dt(plan.cancelled_at)}: ${plan.cancel_reason ?? ""}`
            : `A confirmed plan is a production commitment${plan.confirmed_by ? ` (confirmed by ${plan.confirmed_by} at ${dt(plan.confirmed_at)})` : ""}.`}
        </div>
      )}
      {plan.origin === "agent" && !readOnly && (
        <div className="notice-box info">
          {plan.agent_kind === "draft" ? "Drafted by the planning agent for this period" : "Created by the planning agent from a re-plan proposal"}
          {plan.approved_by ? `, approved by ${plan.approved_by}` : ""} in the <Link to="/actions">Action Center</Link>. Review it like any other plan before confirming.
          {(plan.draft_notes ?? []).map((n) => <span key={n}> {n}</span>)}
        </div>
      )}
      <Card>
        <ol className="wizard-steps" aria-label="Plan steps">
          {steps.map((s) => {
            const locked = !s.ready && s.step > step;
            return (
              <li key={s.step}>
                <button
                  className={s.step === step ? "on" : locked ? "locked" : ""}
                  aria-current={s.step === step ? "step" : undefined}
                  title={locked ? s.blocked_by ?? undefined : undefined}
                  onClick={() => goTo(s.step)}
                >
                  {locked && <span aria-label="locked">🔒 </span>}
                  {s.step}. {s.name || STEPS[s.step - 1]}
                </button>
              </li>
            );
          })}
        </ol>
        {step < 6 && <p className="muted small">{next && !next.ready ? `Next step locked: ${next.blocked_by ?? "prerequisites missing"}` : "All prerequisites for the next step are met."}</p>}
      </Card>
      {notice && <div className={`notice-box ${notice.tone}`} role={notice.tone === "bad" ? "alert" : "status"}>{notice.text}</div>}
      {step === 1 && <DemandStep {...props} />}
      {step === 2 && <PlanStep {...props} />}
      {step === 3 && <WorkOrderStep {...props} result={results.generate} />}
      {step === 4 && <ScheduleStep {...props} result={results.schedule} />}
      {step === 5 && <ResourceStep {...props} />}
      {step === 6 && <ConfirmStep {...props} />}
      <nav className="wizard-nav">
        <button onClick={() => goTo(step - 1)} disabled={step === 1}>‹ Previous</button>
        <button className="primary" onClick={() => goTo(step + 1)} disabled={step === 6}>Next ›</button>
      </nav>
      <CancelDialog open={cancelling} plan={plan} onClose={() => setCancelling(false)} run={run} />
    </>
  );
}
