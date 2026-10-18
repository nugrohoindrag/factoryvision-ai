/**
 * Production plan wizard, six steps: ① Demand → ② Production Plan → ③ Work Order → ④ Scheduling → ⑤ Resource → ⑥ Confirmation.
 *
 * Nothing is typed twice: every step starts from what the earlier ones produced (open order lines, forecast, capacity,
 * work orders). The server decides which steps can open and why not, keeps the step the planner is on, and computes the
 * capacity status of every line; the page only shows it.
 */

import { Fragment, useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { Badge, Card, Dialog, Field, Kpi, Source, Status, Table, num, pct } from "../components/ui";
import { ApiError, del, errorText, get, patch, post } from "../lib/api";
import { useApp } from "../lib/store";
import { CapacityStatus, PlanStatus } from "./ProductionPlans";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;
type Res = { plan: Any; capacity: Any; candidates: Any[]; notes: string[]; [k: string]: Any };
type Run = (key: string, call: () => Promise<Res>) => Promise<Res | null>;

const BASE = "/api/production-plans";
const WO_TONE: Record<string, "neutral" | "info" | "good" | "warn"> = { Draft: "neutral", Scheduled: "info", Confirmed: "good", "In Production": "warn", Completed: "neutral" };
const at = (iso: string | null | undefined) => (iso ? `${iso.slice(5, 10)} ${iso.slice(11, 16)}` : "–");

/** The error text plus the fields the server named, so a refusal says what to change. */
function describe(e: unknown): string {
  const fields = e instanceof ApiError ? (e.body.detail as Any)?.fields : null;
  return fields?.length ? `${e instanceof ApiError ? e.body.message : ""} ${fields.map((f: Any) => f.message).join(" ")}` : errorText(e);
}

function Steps({ steps, current, onJump }: { steps: Any[]; current: number; onJump: (s: number) => void }) {
  return (
    <ol className="steps" aria-label="Wizard steps">
      {steps.map((s) => {
        const state = s.step === current ? "active" : s.step < current && s.reachable ? "done" : "";
        const inner = (
          <>
            <span className="dot">{s.step < current && s.reachable ? "✓" : s.reachable ? s.step : "🔒"}</span>
            {s.label}
          </>
        );
        return (
          <li key={s.step} className={state} aria-current={s.step === current ? "step" : undefined} title={s.reachable ? undefined : s.blocked_by}>
            {s.reachable && s.step !== current ? <button onClick={() => onJump(s.step)}>{inner}</button> : inner}
          </li>
        );
      })}
    </ol>
  );
}

function Section({ title, intro, actions, children }: { title: string; intro: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <Card title={title} actions={actions}>
      <p className="muted small">{intro}</p>
      {children}
    </Card>
  );
}

/* --- Step 1: Demand ------------------------------------------------------------------------------------------- */

function DemandStep({ res, run, readOnly }: { res: Res; run: Run; readOnly: boolean }) {
  const { plan, candidates } = res;
  const [qty, setQty] = useState<Record<string, string>>({});
  const [fcSku, setFcSku] = useState("FB-ARKA");
  const [preview, setPreview] = useState<Any>(null);
  const [fcError, setFcError] = useState<string | null>(null);
  const due = candidates.filter((c) => c.in_period);
  const id = plan.plan_id;
  const add = (c: Any) => run(`add-${c.order_id}`, () => post(`${BASE}/${id}/demands`, { order_id: c.order_id, demand_qty: qty[c.order_id] ? Number(qty[c.order_id]) : null }));
  const addAll = async () => {
    for (const c of due) if (!(await run("add-all", () => post(`${BASE}/${id}/demands`, { order_id: c.order_id })))) break;
  };
  const showForecast = async () => {
    setFcError(null);
    try {
      setPreview(await get(`${BASE}/${id}/forecast/${fcSku}`));
    } catch (e) {
      setFcError(errorText(e));
    }
  };
  const inPlan = plan.lines.flatMap((l: Any) => [
    ...l.sources.map((s: Any) => ({ ...s, kind: "order" })),
    ...(l.forecast_qty ? [{ kind: "forecast", sku: l.sku, demand_qty: l.forecast_qty, line_id: l.line_id }] : []),
  ]);
  return (
    <Section
      title="Step 1 — Demand"
      intro="Pick the open customer order lines this plan produces for. Each order line keeps its own demand row; lines of the same product become one plan line. Adding demand raises the order line's planned quantity, so the order shows as Planned."
      actions={!readOnly && due.length > 0 && <button className="primary" onClick={addAll}>Add all {due.length} due in the period</button>}
    >
      <h3>In this plan ({inPlan.length})</h3>
      <Table
        empty="No demand yet. Add order lines below, or forecast that is not ordered yet."
        cols={[
          { key: "src", label: "Source" },
          { key: "cust", label: "Customer" },
          { key: "sku", label: "Product" },
          { key: "qty", label: "Demand", align: "right" },
          { key: "ship", label: "Delivery" },
          { key: "act", label: "" },
        ]}
        rows={inPlan.map((d: Any) => ({
          src: d.kind === "order" ? <strong>{d.order_number}</strong> : <Badge tone="info">Forecast, not yet ordered</Badge>,
          cust: d.customer_name ?? <span className="muted">–</span>,
          sku: d.sku,
          qty: num(d.demand_qty),
          ship: d.requested_delivery_date ?? <span className="muted">by {plan.period_end}</span>,
          act: !readOnly && (
            <button
              className="ghost small"
              onClick={() =>
                d.kind === "order"
                  ? run(`rm-${d.demand_id}`, () => del(`${BASE}/${id}/demands/${d.demand_id}`))
                  : run(`rm-fc-${d.sku}`, () => post(`${BASE}/${id}/forecast-demand`, { sku: d.sku, forecast_qty: 0 }))
              }
            >
              Remove
            </button>
          ),
        }))}
      />
      {!readOnly && (
        <>
          <h3>Open demand ({candidates.length})</h3>
          <p className="muted small">Order lines with quantity not yet in any plan (ordered − planned &gt; 0). Lines due inside the period come first.</p>
          <Table
            empty="No open demand: every open order line is already planned."
            cols={[
              { key: "order", label: "Order" },
              { key: "cust", label: "Customer" },
              { key: "sku", label: "Product" },
              { key: "open", label: "Open", align: "right" },
              { key: "ship", label: "Delivery" },
              { key: "qty", label: "Add qty" },
              { key: "act", label: "" },
            ]}
            rows={candidates.map((c) => ({
              order: (
                <>
                  <strong>{c.order_number}</strong>
                  {c.at_risk && <span className="risk" title="At risk of being late (Delivery ATP)"> ⚠</span>}
                </>
              ),
              cust: c.customer_name,
              sku: c.sku,
              open: `${num(c.outstanding_qty)} of ${num(c.ordered_qty)}`,
              ship: c.in_period ? c.requested_delivery_date : <span className="muted">{c.requested_delivery_date} (after period)</span>,
              qty: (
                <input
                  type="number"
                  min={1}
                  max={c.outstanding_qty}
                  style={{ width: "6.5rem" }}
                  aria-label={`Quantity for ${c.order_number} ${c.sku}`}
                  placeholder={String(c.outstanding_qty)}
                  value={qty[c.order_id] ?? ""}
                  onChange={(e) => setQty({ ...qty, [c.order_id]: e.target.value })}
                />
              ),
              act: <button className="small" onClick={() => add(c)}>Add</button>,
            }))}
          />
          <h3>Forecast not yet ordered</h3>
          <div className="row">
            <select aria-label="Forecast SKU" value={fcSku} onChange={(e) => (setFcSku(e.target.value), setPreview(null))}>
              {["FB-ARKA", "RB-ARKA", "FB-BROMO", "RB-BROMO", "FB-CITRA", "RB-CITRA"].map((s) => <option key={s}>{s}</option>)}
            </select>
            <button className="small" onClick={showForecast}>Show forecast for the period</button>
            {preview && (
              <>
                <span className="small">
                  <strong>{num(preview.qty)}</strong> pcs not yet ordered ({preview.engine})
                </span>
                <button className="small primary" disabled={!preview.qty} onClick={() => run("fc", () => post(`${BASE}/${id}/forecast-demand`, { sku: fcSku }))}>
                  Add as demand
                </button>
              </>
            )}
          </div>
          {fcError && <p className="error small">{fcError}</p>}
          {preview && (
            <p className="muted small">
              Per forecast week: {preview.weeks.map((w: Any) => `${w.week_start} ${num(w.qty)} (${Math.round(w.share * 100)}% of ${num(w.uncovered_qty)})`).join(" · ") || "no forecast week in the period"}.
              Forecast numbers come from the forecast engine, not from Gemini.
            </p>
          )}
        </>
      )}
    </Section>
  );
}

/* --- Step 2: planned quantity and capacity ---------------------------------------------------------------------- */

function CapacityDetail({ cap, util }: { cap: Any; util: number }) {
  if (!cap) return null;
  return (
    <div>
      <div className="fields">
        <Field label="Total capacity">{num(cap.total_capacity)} pcs</Field>
        <Field label={`Planning (${util}%)`}>{num(cap.planning_capacity)} pcs</Field>
        <Field label="Buffer">{num(cap.buffer)} pcs</Field>
        <Field label="Gap">{num(cap.gap)} pcs</Field>
        <Field label="Utilization">{pct(cap.utilization_pct)}</Field>
      </div>
      <Table
        cols={[
          { key: "m", label: "Machine" },
          { key: "free", label: "Free minutes", align: "right" },
          { key: "cyc", label: "Actual cycle", align: "right" },
          { key: "cap", label: "Could make", align: "right" },
          { key: "use", label: "Takes", align: "right" },
        ]}
        rows={cap.contributions.map((c: Any) => ({
          m: (
            <>
              {c.machine_id} {c.home ? <span className="muted small">(mold is here)</span> : <span className="muted small">(+90 min changeover)</span>}
            </>
          ),
          free: num(c.free_minutes),
          cyc: `${c.cycle_s} s`,
          cap: num(c.capacity_qty),
          use: c.allocated_qty ? <strong>{num(c.allocated_qty)}</strong> : "–",
        }))}
      />
      {cap.uncomputed_machines.length > 0 && (
        <p className="notice-box warn small">
          Not computed: {cap.uncomputed_machines.map((u: Any) => `${u.machine_id} — ${u.reason}`).join("; ")}
        </p>
      )}
    </div>
  );
}

function LineRow({ line, util, run, planId, readOnly }: { line: Any; util: number; run: Run; planId: string; readOnly: boolean }) {
  const [planned, setPlanned] = useState(String(line.planned_qty));
  const [prio, setPrio] = useState(String(line.priority));
  const [open, setOpen] = useState(false);
  useEffect(() => {
    setPlanned(String(line.planned_qty));
    setPrio(String(line.priority));
  }, [line.planned_qty, line.priority]);
  const dirty = planned !== String(line.planned_qty) || prio !== String(line.priority);
  const demand = line.demand_qty + line.forecast_qty;
  return (
    <>
      <tr>
        <td>
          <button className="ghost small" aria-expanded={open} aria-label={`Capacity of ${line.sku}`} onClick={() => setOpen(!open)}>{open ? "▾" : "▸"}</button>
        </td>
        <td>
          <strong>{line.sku}</strong> <span className="muted small">{line.product_name}</span>
        </td>
        <td className="num">
          {num(demand)}
          {line.forecast_qty > 0 && <div className="muted small">{num(line.demand_qty)} orders + {num(line.forecast_qty)} forecast</div>}
        </td>
        <td>
          <input type="number" min={0} style={{ width: "7rem" }} aria-label={`Planned ${line.sku}`} value={planned} disabled={readOnly} onChange={(e) => setPlanned(e.target.value)} />
          {Number(planned) < demand && <div className="muted small">{num(demand - Number(planned))} below demand</div>}
        </td>
        <td>
          <input type="number" min={1} style={{ width: "4rem" }} aria-label={`Priority ${line.sku}`} value={prio} disabled={readOnly} onChange={(e) => setPrio(e.target.value)} />
        </td>
        <td>{line.required_delivery_date ?? "–"}</td>
        <td title="Computed by the server from demand against capacity">
          <CapacityStatus status={line.capacity_status} />
        </td>
        <td>
          {!readOnly && (
            <button className="small" disabled={!dirty} onClick={() => run(`line-${line.line_id}`, () => patch(`${BASE}/${planId}/lines/${line.line_id}`, { planned_qty: Number(planned), priority: Number(prio) }))}>
              Save
            </button>
          )}
        </td>
      </tr>
      {open && (
        <tr className="sub">
          <td colSpan={8}>
            <CapacityDetail cap={line.capacity} util={util} />
          </td>
        </tr>
      )}
    </>
  );
}

function PlanStep({ res, run, readOnly }: { res: Res; run: Run; readOnly: boolean }) {
  const { plan, capacity } = res;
  return (
    <Section
      title="Step 2 — Production Plan"
      intro="Decide how much to make and in which order. Planned can stay below demand; the gap stays visible. Lines take molding capacity in priority order (1 first), so a line's status depends on the lines above it."
    >
      <div className="table-wrap">
        <table className="order-table">
          <thead>
            <tr>
              <th aria-label="Expand" />
              <th>Product</th>
              <th className="num">Demand</th>
              <th>Planned</th>
              <th>Priority</th>
              <th>Delivery</th>
              <th>Capacity</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {plan.lines.map((l: Any) => (
              <LineRow key={l.line_id} line={l} util={plan.planning_utilization_pct} run={run} planId={plan.plan_id} readOnly={readOnly} />
            ))}
          </tbody>
        </table>
      </div>
      {capacity && (
        <Source>
          {capacity.rule}. {capacity.production_days} production days from today, {num(capacity.minutes_per_machine)} available minutes per machine; minutes already promised to orders
          outside this plan: {Object.entries(capacity.committed_minutes).map(([m, v]) => `${m} ${num(v as number)}`).join(", ")}. {capacity.source}.
        </Source>
      )}
    </Section>
  );
}

/* --- Steps 3 to 5: work orders, schedule, resources -------------------------------------------------------------- */

function WoStatus({ status }: { status: string }) {
  return <Badge tone={WO_TONE[status] ?? "neutral"}>{status}</Badge>;
}

function WorkOrderStep({ res, run, readOnly }: { res: Res; run: Run; readOnly: boolean }) {
  const { plan } = res;
  const g = res.generated;
  return (
    <Section
      title="Step 3 — Work Order"
      intro="One work order per process of the routing (molding → painting → assembly) for each plan line, each for the line's planned quantity, chained by predecessor. Generating again creates no duplicates."
      actions={!readOnly && <button className="primary" onClick={() => run("gen", () => post(`${BASE}/${plan.plan_id}/work-orders/generate`))}>Generate work orders</button>}
    >
      {g && (
        <p className="notice-box info small">
          {g.created.length} created, {g.existing.length} already existed{g.skipped_skus.length ? `, skipped (planned 0): ${g.skipped_skus.join(", ")}` : ""}.
        </p>
      )}
      <Table
        empty="No work orders yet. Generate them from the plan lines."
        cols={[
          { key: "wo", label: "Work order" },
          { key: "sku", label: "Product" },
          { key: "seq", label: "Seq", align: "right" },
          { key: "proc", label: "Process" },
          { key: "qty", label: "Planned", align: "right" },
          { key: "pre", label: "Predecessor" },
          { key: "st", label: "Status" },
        ]}
        rows={plan.work_orders.map((w: Any) => ({
          wo: <strong>{w.wo_id}</strong>,
          sku: w.sku,
          seq: w.sequence,
          proc: w.process_name,
          qty: num(w.planned_qty),
          pre: w.predecessor_wo_id ?? <span className="muted">first process</span>,
          st: <WoStatus status={w.status} />,
        }))}
      />
    </Section>
  );
}

function ScheduleStep({ res, run, readOnly }: { res: Res; run: Run; readOnly: boolean }) {
  const { plan } = res;
  const s = plan.schedule_run;
  const [open, setOpen] = useState<Set<string>>(new Set());
  return (
    <Section
      title="Step 4 — Scheduling"
      intro="OR-Tools CP-SAT places molding per machine and day: compatible machines only, one mold on one machine a day, 90 minutes per mold change, the time already promised to other orders kept. Painting follows one shift behind molding on the paint line, assembly behind painting."
      actions={!readOnly && <button className="primary" onClick={() => run("sched", () => post(`${BASE}/${plan.plan_id}/schedule`))}>{s ? "Run the scheduler again" : "Run the scheduler"}</button>}
    >
      {plan.schedule_stale && <p className="notice-box warn small">A molding machine was changed in step 5. Run the scheduler again to check capacity there.</p>}
      {s && (
        <>
          <div className="kpis">
            <Kpi label="Fill rate" value={pct(s.fill_rate_pct)} sub={Object.keys(s.short_qty).length ? `short: ${Object.entries(s.short_qty).map(([k, v]) => `${k} ${num(v as number)}`).join(", ")}` : "every planned piece fits"} tone={s.fill_rate_pct < 100 ? "bad" : "good"} />
            <Kpi label="Late for delivery" value={s.late_work_orders.length} sub={s.late_work_orders.map((l: Any) => l.sku).join(", ") || "all on time"} tone={s.late_work_orders.length ? "warn" : "good"} />
            <Kpi label="Mold changes" value={s.changeovers} sub={s.mold_moves.join(", ") || "no mold moves"} />
            <Kpi label="Solver" value={`${s.solve_seconds} s`} sub={`${s.status} · ${s.production_days} production days`} />
          </div>
          {s.late_work_orders.length > 0 && (
            <ul className="notice-box warn small">
              {s.late_work_orders.map((l: Any) => (
                <li key={l.wo_id}>
                  {l.sku}: {l.dates.map((d: Any) => `${num(d.short_qty)} pcs not molded 2 days before the ${d.delivery_date} delivery`).join("; ")}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      <div className="table-wrap">
        <table className="order-table">
          <thead>
            <tr>
              <th aria-label="Expand" />
              <th>Work order</th>
              <th>Process</th>
              <th>Machine</th>
              <th>Planned start</th>
              <th>Planned end</th>
              <th>Shift</th>
              <th className="num">Qty</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {plan.work_orders.map((w: Any) => (
              <Fragment key={w.wo_id}>
                <tr>
                  <td>
                    {w.runs.length > 0 && (
                      <button className="ghost small" aria-expanded={open.has(w.wo_id)} aria-label={`Runs of ${w.wo_id}`} onClick={() => setOpen((o) => { const x = new Set(o); if (x.has(w.wo_id)) x.delete(w.wo_id); else x.add(w.wo_id); return x; })}>
                        {open.has(w.wo_id) ? "▾" : "▸"}
                      </button>
                    )}
                  </td>
                  <td><strong>{w.wo_id}</strong></td>
                  <td>{w.process_name}</td>
                  <td>{w.machine_id ?? <span className="muted">not set</span>}</td>
                  <td>{at(w.planned_start)}</td>
                  <td>{at(w.planned_end)}</td>
                  <td>{w.shift ?? "–"}</td>
                  <td className="num">
                    {num(w.planned_qty)}
                    {w.short_qty > 0 && <div className="risk small">{num(w.short_qty)} short</div>}
                  </td>
                  <td><WoStatus status={w.status} /></td>
                </tr>
                {open.has(w.wo_id) && (
                  <tr className="sub">
                    <td colSpan={9}>
                      <Table
                        cols={[{ key: "d", label: "Day" }, { key: "m", label: "Machine" }, { key: "q", label: "Qty", align: "right" }, { key: "t", label: "From – to" }, { key: "c", label: "" }]}
                        rows={w.runs.map((r: Any) => ({ d: r.date, m: r.machine_id, q: num(r.qty), t: `${at(r.start)} – ${at(r.end)}`, c: r.changeover ? <Badge tone="warn">mold change</Badge> : "" }))}
                      />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {s && <Source>{s.engine}; {num(s.assumptions.minutes_per_workday)} min per working day × {s.assumptions.available_factor} available; paint takt {s.assumptions.paint_takt_s} s, assembly takt {s.assumptions.assembly_takt_s} s per cell (planning assumptions); cycle and yield from fv.fact_production, last 7 days.</Source>}
    </Section>
  );
}

function ResourceStep({ res, run, readOnly }: { res: Res; run: Run; readOnly: boolean }) {
  const { plan } = res;
  const missing = plan.work_orders.filter((w: Any) => !w.resourced);
  const set = (w: Any, body: Any) => run(`res-${w.wo_id}`, () => patch(`${BASE}/${plan.plan_id}/work-orders/${w.wo_id}`, body));
  return (
    <Section title="Step 5 — Resource" intro="Machine and mold per work order. Only machines and molds compatible with the product are offered; the server refuses any other.">
      {missing.length > 0 && <p className="notice-box warn small">{missing.length} work order(s) have no machine or mold yet. Confirmation stays locked until all have one.</p>}
      <Table
        cols={[
          { key: "wo", label: "Work order" },
          { key: "proc", label: "Process" },
          { key: "m", label: "Machine" },
          { key: "mold", label: "Mold" },
        ]}
        rows={plan.work_orders.map((w: Any) => ({
          wo: <strong>{w.wo_id}</strong>,
          proc: w.process_name,
          m: (
            <select aria-label={`Machine for ${w.wo_id}`} value={w.machine_id ?? ""} disabled={readOnly || w.status === "Confirmed"} onChange={(e) => set(w, { machine_id: e.target.value })}>
              {!w.machine_id && <option value="">not set</option>}
              {w.options.machines.map((m: string) => <option key={m}>{m}</option>)}
            </select>
          ),
          mold: w.options.molds.length ? (
            <select aria-label={`Mold for ${w.wo_id}`} value={w.mold_id ?? ""} disabled={readOnly || w.status === "Confirmed"} onChange={(e) => set(w, { mold_id: e.target.value })}>
              {!w.mold_id && <option value="">not set</option>}
              {w.options.molds.map((m: string) => <option key={m}>{m}</option>)}
            </select>
          ) : (
            <span className="muted">no mold for this process</span>
          ),
        }))}
      />
    </Section>
  );
}

/* --- Step 6: confirmation ---------------------------------------------------------------------------------------- */

function ConfirmStep({ res, run, readOnly }: { res: Res; run: Run; readOnly: boolean }) {
  const { plan } = res;
  const r = plan.readiness;
  const unconfirmed = plan.work_orders.filter((w: Any) => w.status !== "Confirmed" && w.status !== "In Production").length;
  const late = plan.schedule_run?.late_work_orders ?? [];
  return (
    <Section
      title="Step 6 — Confirmation"
      intro="Confirming turns the plan into a production commitment: its lines are released and it no longer changes. It is refused while a line is Capacity Up Required or a work order is not confirmed."
    >
      <div className="kpis">
        <Kpi label="Plan lines" value={plan.summary.line_count} />
        <Kpi label="Total planned" value={num(plan.summary.total_planned_qty)} sub={`demand ${num(plan.summary.total_demand_qty)}`} />
        <Kpi label="Work orders" value={plan.summary.work_order_count} sub={`${r.confirmed_work_orders} confirmed`} />
        <Kpi label="Customer orders served" value={plan.summary.customer_orders.length} sub={plan.summary.customer_orders.slice(0, 3).join(", ") + (plan.summary.customer_orders.length > 3 ? " …" : "")} />
      </div>
      {r.capacity_up_required_lines.length > 0 && (
        <p className="notice-box bad small">
          Capacity Up Required: {r.capacity_up_required_lines.join(", ")}. Lower the planned quantity in step 2, or add capacity (extra shift, mold move) before confirming.
        </p>
      )}
      {late.length > 0 && <p className="notice-box warn small">The schedule misses delivery dates for {late.map((l: Any) => l.sku).join(", ")}. Confirming keeps the plan; Delivery shows the orders at risk.</p>}
      {!readOnly && unconfirmed > 0 && <p className="notice-box warn small">{unconfirmed} work order(s) are not confirmed yet. Confirm them before the plan.</p>}
      {!readOnly && (
        <div className="dialog-foot">
          <button disabled={unconfirmed === 0} onClick={() => run("wo-confirm", () => post(`${BASE}/${plan.plan_id}/work-orders/confirm`))}>Confirm all work orders</button>
          <button className="primary" onClick={() => run("confirm", () => post(`${BASE}/${plan.plan_id}/confirm`))}>Confirm production plan</button>
        </div>
      )}
      {plan.confirmed_at && (
        <p className="notice-box good small">
          Confirmed by {plan.confirmed_by} at {plan.confirmed_at.slice(0, 16).replace("T", " ")}. The served orders show as Planned; work orders that started today are In Production.
        </p>
      )}
    </Section>
  );
}

/* --- page ------------------------------------------------------------------------------------------------------- */

function CancelDialog({ open, plan, onClose, run }: { open: boolean; plan: Any; onClose: () => void; run: Run }) {
  const [reason, setReason] = useState("");
  const send = async () => {
    if (await run("cancel", () => post(`${BASE}/${plan.plan_id}/cancel`, { reason }))) {
      setReason("");
      onClose();
    }
  };
  return (
    <Dialog open={open} title={`Cancel ${plan.plan_number}`} onClose={onClose}>
      <p className="small">The plan's demand goes back to the customer order lines, so they can enter another plan. A plan whose work orders are already in production cannot be cancelled.</p>
      <div className="form-grid">
        <label>
          Reason
          <textarea aria-label="Cancellation reason" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
      </div>
      <div className="dialog-foot">
        <button onClick={onClose}>Keep the plan</button>
        <button className="primary" disabled={reason.trim().length < 3} onClick={send}>Cancel plan</button>
      </div>
    </Dialog>
  );
}

function BaselinePlan({ plan }: { plan: Any }) {
  return (
    <Card title="Plan lines (OEM monthly schedule)">
      <p className="muted small">{plan.locked_reason}</p>
      <Table
        cols={[
          { key: "sku", label: "Product" },
          { key: "qty", label: "Planned", align: "right" },
          { key: "m", label: "Machine" },
          { key: "load", label: "Machine load", align: "right" },
          { key: "cap", label: "Capacity" },
          { key: "st", label: "Line" },
        ]}
        rows={plan.lines.map((l: Any) => ({
          sku: (
            <>
              <strong>{l.sku}</strong> <span className="muted small">{l.product_name}</span>
            </>
          ),
          qty: num(l.planned_qty),
          m: l.machine_id,
          load: pct(l.load_pct),
          cap: <CapacityStatus status={l.capacity_status} />,
          st: l.status,
        }))}
      />
      <Source>{plan.source}</Source>
    </Card>
  );
}

export default function PlanWizardPage() {
  const { planId = "" } = useParams();
  const { version } = useApp();
  const [res, setRes] = useState<Res | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);

  useEffect(() => {
    let alive = true;
    get<Res>(`${BASE}/${planId}`)
      .then((r) => {
        if (!alive) return;
        setRes(r);
        setError(null);
        setStep((s) => s ?? (r.plan.baseline ? null : r.plan.readiness.resume_step));
      })
      .catch((e) => alive && setError(errorText(e)));
    return () => {
      alive = false;
    };
  }, [planId, version]);

  const run: Run = useCallback(async (key, call) => {
    setBusy(key);
    setActionError(null);
    try {
      const r = await call();
      setRes(r);
      return r;
    } catch (e) {
      setActionError(describe(e));
      return null;
    } finally {
      setBusy(null);
    }
  }, []);

  if (error) return <p className="error" role="alert">Could not load the production plan: {error}</p>;
  if (!res) return <Status loading error={null} />;
  const { plan } = res;
  const readOnly = !plan.editable;
  const goTo = (s: number) => {
    setStep(s);
    if (!readOnly) patch<Res>(`${BASE}/${plan.plan_id}`, { wizard_step: s }).then(setRes).catch((e) => setActionError(describe(e)));
  };
  const cur = step ?? 1;
  const props = { res, run, readOnly };
  return (
    <>
      <p className="small"><Link to="/production-plans">← Production plans</Link></p>
      <div className="page-head">
        <h1>
          {plan.plan_number} <PlanStatus status={plan.status} />
        </h1>
        {!plan.baseline && !["Cancelled", "Completed"].includes(plan.status) && (
          <button className="ghost" onClick={() => setCancelling(true)}>Cancel plan</button>
        )}
      </div>
      <Card>
        <div className="fields">
          <Field label="Period">{plan.period_start} – {plan.period_end}</Field>
          <Field label="Capacity">
            <CapacityStatus status={plan.capacity_status} />
          </Field>
          <Field label="Planning utilization">{plan.planning_utilization_pct}%</Field>
          <Field label="Forecast snapshot">{plan.forecast_id ?? "live forecast"}</Field>
          <Field label="Capacity snapshot">{plan.capacity_plan_id ?? "computed now"}</Field>
          <Field label="Created by">{plan.created_by}</Field>
        </div>
        {plan.created_by === "production_planning_agent" && (
          <p className="notice-box info small">
            Drafted by the Production Planning agent{plan.approved_by ? `, approved by ${plan.approved_by} in the Action Center` : ""}. Check the lines, then schedule and confirm.
            {(plan.draft_notes ?? []).map((n: string) => <span key={n}> {n}</span>)}
          </p>
        )}
        {plan.cancel_reason && <p className="notice-box bad small">Cancelled by {plan.cancelled_by}: {plan.cancel_reason}</p>}
        {readOnly && !plan.baseline && <p className="notice-box info small">{plan.locked_reason}</p>}
      </Card>
      {plan.baseline ? (
        <BaselinePlan plan={plan} />
      ) : (
        <>
          <Card>
            <Steps steps={plan.readiness.steps} current={cur} onJump={goTo} />
          </Card>
          {busy && <p className="muted small" role="status">Working…</p>}
          {actionError && <p className="error" role="alert">{actionError}</p>}
          {res.notes.map((n) => <p key={n} className="notice-box info small">{n}</p>)}
          {cur === 1 && <DemandStep {...props} />}
          {cur === 2 && <PlanStep {...props} />}
          {cur === 3 && <WorkOrderStep {...props} />}
          {cur === 4 && <ScheduleStep {...props} />}
          {cur === 5 && <ResourceStep {...props} />}
          {cur === 6 && <ConfirmStep {...props} />}
          <div className="dialog-foot">
            <button disabled={cur === 1} onClick={() => goTo(cur - 1)}>‹ Previous</button>
            <button
              className="primary"
              disabled={cur === 6 || !plan.readiness.steps[cur]?.reachable}
              title={plan.readiness.steps[cur]?.reachable ? undefined : plan.readiness.steps[cur]?.blocked_by}
              onClick={() => goTo(cur + 1)}
            >
              Next ›
            </button>
          </div>
          {cur < 6 && !plan.readiness.steps[cur]?.reachable && <p className="muted small right">{plan.readiness.steps[cur]?.blocked_by}</p>}
        </>
      )}
      {!plan.baseline && <CancelDialog open={cancelling} plan={plan} onClose={() => setCancelling(false)} run={run} />}
    </>
  );
}
