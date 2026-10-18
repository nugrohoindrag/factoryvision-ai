/**
 * Module 2, Production Planning: the list of production plans, the create dialog, and the solver views
 * (molding load, what-if) that were here before plans were stored.
 *
 * A plan is a commitment, so the list leads with what decides whether it can become one: how much it plans to
 * produce and whether capacity allows it. A Draft left for a week is flagged: an abandoned plan is demand nobody
 * is producing. The wizard for one plan is `PlanWizard.tsx`; the capacity page is `Capacity.tsx`.
 */

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AskAgent } from "../components/agents";
import { Bars } from "../components/charts";
import { Badge, Card, Dialog, Kpi, Source, Status, Table, num, pct } from "../components/ui";
import { errorText, post } from "../lib/api";
import { formatIdr } from "../lib/format";
import { useApp, useData } from "../lib/store";
import type { Action, CapacityStatus, PlanDetail, PlanListRow, PlanStatus } from "../lib/types";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

export const PLAN_STATUSES: PlanStatus[] = ["Draft", "Planning", "Ready", "Confirmed", "In Execution", "Completed", "Cancelled"];
/** Statuses after which a plan can no longer change (PP-R09). */
export const FINAL_STATUSES: PlanStatus[] = ["Confirmed", "In Execution", "Completed", "Cancelled"];

type Tone = "neutral" | "good" | "warn" | "bad" | "info";
const PLAN_TONE: Record<string, Tone> = { Draft: "neutral", Planning: "info", Ready: "info", Confirmed: "good", "In Execution": "warn", Completed: "good", Cancelled: "neutral" };
const CAPACITY_TONE: Record<CapacityStatus, Tone> = { "Within Plan": "good", "Additional Demand": "warn", "Capacity Up Required": "bad" };
export const CAPACITY_HINT: Record<CapacityStatus, string> = {
  "Within Plan": "Demand fits within planning capacity.",
  "Additional Demand": "Demand is above planning capacity but still fits in the buffer.",
  "Capacity Up Required": "Demand exceeds total capacity.",
};

export function PlanStatusBadge({ status }: { status: string }) {
  return <Badge tone={PLAN_TONE[status] ?? "neutral"}>{status}</Badge>;
}

export function CapacityBadge({ status }: { status: CapacityStatus | null | undefined }) {
  if (!status) return <span className="muted">–</span>;
  return (
    <span title={`${CAPACITY_HINT[status]} Set by the system, not typed.`}>
      <Badge tone={CAPACITY_TONE[status] ?? "neutral"}>{status}</Badge>
    </span>
  );
}

export function AgentBadge() {
  return (
    <span title="Created by the planning agent from a proposal (re-plan or draft) you approved in the Action Center.">
      <Badge tone="info">Agent</Badge>
    </span>
  );
}

export function ForecastLink({ id }: { id: string | null | undefined }) {
  if (!id) return <span className="muted">–</span>;
  return <Link to={`/forecast?snapshot=${encodeURIComponent(id)}`} title="The forecast snapshot this plan uses">{id}</Link>;
}

const monthEnd = (start: string) => {
  const d = new Date(`${start.slice(0, 7)}-01T00:00:00Z`);
  d.setUTCMonth(d.getUTCMonth() + 1, 0);
  return d.toISOString().slice(0, 10);
};

/** PP-R05: period start and end; the plan is created as Draft and the wizard opens on it. */
export function CreatePlanDialog({ open, month, onClose }: { open: boolean; month: string | null; onClose: () => void }) {
  const { bump } = useApp();
  const navigate = useNavigate();
  const base = (month ?? new Date().toISOString()).slice(0, 7);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const s = start || `${base}-01`;
  const e = end || monthEnd(`${base}-01`);
  const invalid = e < s;
  const close = () => {
    if (busy) return;
    setStart("");
    setEnd("");
    setErr(null);
    onClose();
  };
  const create = async () => {
    setBusy(true);
    setErr(null);
    try {
      const plan = await post<PlanDetail>("/api/plans", { period_start: s, period_end: e });
      bump();
      setBusy(false);
      close();
      navigate(`/planning/${plan.plan_id}`);
    } catch (x) {
      setErr(errorText(x));
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} title="New production plan" onClose={close}>
      <p className="muted small">
        The plan starts as Draft with the next number for its month (PP-2027-02, then PP-2027-02-002). The planning utilization in force now is copied onto it. You can leave the
        wizard and continue any time.
      </p>
      <div className="form-grid">
        <label>
          Period start
          <input type="date" aria-label="Period start" value={s} onChange={(x) => setStart(x.target.value)} />
        </label>
        <label>
          Period end
          <input type="date" aria-label="Period end" value={e} onChange={(x) => setEnd(x.target.value)} className={invalid ? "invalid" : ""} />
          {invalid && <span className="field-error">The period end cannot be before the start.</span>}
        </label>
      </div>
      {err && <p className="error" role="alert">{err}</p>}
      <div className="dialog-foot">
        <button className="ghost" onClick={close} disabled={busy}>Cancel</button>
        <button className="primary" onClick={create} disabled={busy || invalid}>{busy ? "Creating…" : "Create & open wizard"}</button>
      </div>
    </Dialog>
  );
}

/** PP-R81/R84: re-plan proposals from the agent wait in the Action Center until a person approves them. */
function ReplanNotice() {
  const { data } = useData<{ pending: number; actions: Action[] }>("/api/actions");
  const replans = (data?.actions ?? []).filter((a) => a.kind === "replan" && a.status === "pending");
  if (!replans.length) return null;
  return (
    <div className="notice-box info" role="status">
      <strong>{replans.length} re-plan proposal{replans.length > 1 ? "s" : ""} from the planning agent</strong> {replans.length > 1 ? "are" : "is"} waiting for approval:{" "}
      {replans.map((a) => a.title).join("; ")}. <Link to="/actions">Review in the Action Center</Link>. An approved proposal appears here as a new Draft plan marked Agent.
    </div>
  );
}

/** Customer orders behind a plan, from its demand rows (the list endpoint does not carry them). */
function RelatedOrders({ planId }: { planId: string }) {
  const { data } = useData<PlanDetail>(`/api/plans/${encodeURIComponent(planId)}`);
  if (!data) return <span className="muted">…</span>;
  const orders = [...new Set((data.demand ?? []).map((d) => d.order_number))];
  if (!orders.length) return <span className="muted">–</span>;
  return (
    <span className="small" title={orders.join(", ")}>
      {orders.slice(0, 3).join(", ")}
      {orders.length > 3 ? ` +${orders.length - 3}` : ""}
    </span>
  );
}

function PlanList({ onCreate }: { onCreate: () => void }) {
  const navigate = useNavigate();
  const [status, setStatus] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const { bump } = useApp();
  const qs = new URLSearchParams(Object.entries({ status, period_from: from, period_to: to }).filter(([, v]) => v)).toString();
  const { data, error, loading } = useData<{ plans: PlanListRow[] }>(`/api/plans${qs ? `?${qs}` : ""}`);
  const rows = [...(data?.plans ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at));
  const anyFilter = !!(status || from || to);
  return (
    <Card title={data ? `Production plans (${rows.length})` : "Production plans"}>
      <div className="chips" role="group" aria-label="Filter by status">
        <button className={`chip ${status === "" ? "on" : ""}`} aria-pressed={status === ""} onClick={() => setStatus("")}>All</button>
        {PLAN_STATUSES.map((s) => (
          <button key={s} className={`chip ${status === s ? "on" : ""}`} aria-pressed={status === s} onClick={() => setStatus(s)}>{s}</button>
        ))}
      </div>
      <div className="filters">
        <label>
          Period from
          <input type="date" aria-label="Period from" value={from} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label>
          Period to
          <input type="date" aria-label="Period to" value={to} onChange={(e) => setTo(e.target.value)} />
        </label>
      </div>
      {error ? (
        <div className="notice-box bad" role="alert">
          Could not load production plans: {error} <button className="small" onClick={bump}>Retry</button>
        </div>
      ) : !data && loading ? (
        <p className="muted">Loading production plans…</p>
      ) : rows.length === 0 ? (
        anyFilter ? (
          <p className="muted">No plan matches the filter.</p>
        ) : (
          <div className="empty">
            <strong>No production plans yet</strong>
            <p className="muted">Create a plan for a period, then walk the six-step wizard to choose demand, quantities, work orders, the schedule and resources.</p>
            <button className="primary" onClick={onCreate}>+ New plan</button>
          </div>
        )
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Plan</th>
                <th>Period</th>
                <th className="right">Planned qty</th>
                <th>Capacity</th>
                <th>Customer orders</th>
                <th>Forecast</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.plan_id}>
                  <td>
                    <strong>{p.plan_number}</strong>
                    {p.status === "Draft" && p.ageing && (
                      <span className="ageing" role="img" aria-label="Ageing draft" title={`Draft for ${p.age_days} days; this demand is not a commitment yet.`}> ⌛</span>
                    )}
                    {p.origin === "agent" && <> <AgentBadge /></>}
                  </td>
                  <td>{p.period_start} → {p.period_end}</td>
                  <td className="right">{num(p.total_planned)} <span className="muted small">({p.line_count} line{p.line_count === 1 ? "" : "s"})</span></td>
                  <td><CapacityBadge status={p.capacity_status} /></td>
                  <td><RelatedOrders planId={p.plan_id} /></td>
                  <td><ForecastLink id={p.forecast_id} /></td>
                  <td><PlanStatusBadge status={p.status} /></td>
                  <td><button className="ghost small" onClick={() => navigate(`/planning/${p.plan_id}`)}>Open wizard</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Source>session plans (production_plans). Capacity is the worst status among a plan's lines.</Source>
    </Card>
  );
}

/** Molding load per machine and the CP-SAT what-if, from the warehouse plan (PP-R80, PP-R82). */
function SolverViews({ data }: { data: Any }) {
  const [whatIf, setWhatIf] = useState(false);
  const wi = useData<Any>(whatIf ? "/api/modules/planning/what-if" : null);
  const cur = data.current;
  const g = cur?.groups?.[0];
  return (
    <>
      <div className="kpis">
        <Kpi label="Bottleneck" value={cur.bottleneck_machine} sub={`load ${pct(cur.bottleneck_load_pct)}`} tone="bad" />
        <Kpi label="3,500 t group gap" value={pct(g.capacity_gap_pct)} sub={g.status} tone={g.capacity_gap_pct > 0 ? "bad" : "good"} />
        <Kpi label="Demand change applied" value={Object.keys(cur.demand_change_pct).length ? Object.entries(cur.demand_change_pct).map(([k, v]) => `${k} +${v}%`).join(", ") : "none"} />
        <Kpi label="Working days (month)" value={cur.working_days} />
      </div>
      <Card title={`Molding load per machine, ${cur.month.slice(0, 7)}`} actions={<Link to="/capacity">Capacity plan →</Link>}>
        <Bars items={cur.machines.map((m: Any) => ({ label: `${m.machine_id} · ${m.skus.join(", ")}`, value: m.load_pct, tone: m.load_pct > 100 ? "bad" : m.load_pct > 90 ? "warn" : "good" }))} unit="%" threshold={100} />
        <p className="muted small">ARKA molds fit only the 3,500 t machines IMM-04 and IMM-05. A mold move is a swap with a 90-minute changeover.</p>
        <Source>{cur.source}</Source>
      </Card>
      <Card title="What-if (OR-Tools CP-SAT)" actions={!whatIf && <button onClick={() => setWhatIf(true)}>Compare options</button>}>
        {!whatIf && <p className="muted">Compare maintenance, mold moves and Sunday shifts with the solver (takes a few seconds).</p>}
        <Status loading={wi.loading} error={wi.error} />
        {wi.data && (
          <Table
            cols={[{ key: "scenario", label: "Option" }, { key: "fill", label: "Fill rate", align: "right" }, { key: "unmet", label: "Unmet" }, { key: "co", label: "Changeovers", align: "right" }, { key: "cost", label: "Extra cost", align: "right" }]}
            rows={wi.data.scenarios.map((s: Any) => ({ scenario: s.scenario.replaceAll("_", " "), fill: pct(s.fill_rate_pct), unmet: Object.entries(s.unmet_qty).map(([k, v]) => `${k} ${num(v as number)}`).join(", ") || "–", co: s.changeovers, cost: formatIdr(s.extra_cost_idr) }))}
          />
        )}
      </Card>
    </>
  );
}

const addDays = (iso: string, n: number) => new Date(Date.parse(`${iso}T00:00:00Z`) + n * 86_400_000).toISOString().slice(0, 10);

/** The planning agent drafts a plan for a period from open orders, forecast and capacity; the draft goes to the Action Center for approval. */
function AgentDraft() {
  const { bump } = useApp();
  const today = useData<{ as_of: string }>("/api/plans").data?.as_of ?? ""; // the plant's date, not the browser's
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [forecast, setForecast] = useState(true);
  const [draft, setDraft] = useState<Any>(null);
  const [sent, setSent] = useState<Action | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const s = start || today;
  const e = end || (today ? addDays(today, 6) : "");
  const reset = () => (setDraft(null), setSent(null));
  const call = async (path: string) => {
    setBusy(true);
    setErr(null);
    try {
      const r = await post<Any>(path, { period_start: s, period_end: e, include_forecast: forecast });
      if (r.action) {
        setSent(r.action);
        setDraft(r.draft);
        bump();
      } else {
        setDraft(r);
        setSent(null);
      }
    } catch (x) {
      setErr(errorText(x));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Draft with the planning agent" actions={<AgentBadge />}>
      <p className="muted small">
        The agent takes every open customer order line due in the period, adds forecast not yet ordered, sets priorities (firm orders first, earliest delivery first) and
        checks capacity. A line above capacity is planned at what fits and its gap is shown. The draft goes to the Action Center; after approval it opens as a Draft plan
        you finish in the wizard.
      </p>
      <div className="filters">
        <label>
          Period start
          <input type="date" aria-label="Draft period start" value={s} onChange={(x) => (setStart(x.target.value), reset())} />
        </label>
        <label>
          Period end
          <input type="date" aria-label="Draft period end" value={e} onChange={(x) => (setEnd(x.target.value), reset())} />
        </label>
        <label className="row small">
          <input type="checkbox" checked={forecast} onChange={(x) => (setForecast(x.target.checked), reset())} /> Include forecast not yet ordered
        </label>
      </div>
      <div className="row">
        <button onClick={() => call("/api/plans/draft")} disabled={busy || !s || !e}>{busy && !draft ? "Drafting…" : "Prepare draft"}</button>
        {draft && !sent && (
          <button className="primary" onClick={() => call("/api/plans/draft/propose")} disabled={busy}>Send draft for approval</button>
        )}
        {sent && (
          <span className="small">
            Pending in the <Link to="/actions">Action Center</Link>. Approve it there to open it as a Draft plan.
          </span>
        )}
      </div>
      {err && <p className="error" role="alert">{err}</p>}
      {draft && (
        <>
          <div className="kpis">
            <Kpi label="Order lines" value={num(draft.summary.order_lines)} sub={`${num(draft.summary.customer_orders)} customer orders, ${num(draft.summary.order_qty)} pcs`} />
            <Kpi label="Forecast not yet ordered" value={num(draft.summary.forecast_qty)} sub={draft.forecast_id ?? "no forecast snapshot"} />
            <Kpi label="Planned" value={num(draft.summary.planned_qty)} sub={draft.summary.worst_capacity_status ?? "no line"} tone={draft.gaps.length ? "warn" : "good"} />
            <Kpi label="Capacity gaps" value={draft.gaps.length} sub={draft.gaps.map((g: Any) => `${g.sku} −${num(g.gap_qty)}`).join(", ") || "everything fits"} tone={draft.gaps.length ? "bad" : "good"} />
          </div>
          <Table
            empty="Nothing to plan in this period."
            cols={[
              { key: "p", label: "Priority", align: "right" },
              { key: "sku", label: "Product" },
              { key: "orders", label: "Orders", align: "right" },
              { key: "fc", label: "Forecast", align: "right" },
              { key: "planned", label: "Planned", align: "right" },
              { key: "cap", label: "Total capacity", align: "right" },
              { key: "st", label: "Capacity" },
            ]}
            rows={draft.lines.map((l: Any) => ({
              p: l.priority,
              sku: (
                <>
                  <strong>{l.sku}</strong> <span className="muted small">{l.product_name}</span>
                </>
              ),
              orders: `${num(l.demand_qty)} (${l.orders})`,
              fc: num(l.forecast_qty),
              planned: (
                <>
                  {num(l.planned_qty)}
                  {l.short_of_orders + l.short_of_forecast > 0 && <div className="risk small">{num(l.short_of_orders + l.short_of_forecast)} not planned</div>}
                </>
              ),
              cap: num(l.total_capacity),
              st: <CapacityBadge status={l.capacity_status} />,
            }))}
          />
          {draft.gaps.length > 0 && (
            <p className="notice-box warn small">
              Over capacity, planned at what fits: {draft.gaps.map((g: Any) => `${g.sku} ${num(g.gap_qty)} pcs short${g.short_of_orders ? ` (${num(g.short_of_orders)} of them firm orders)` : ""}`).join("; ")}.
              Options: an extra shift, a mold move, or less forecast. Ask the agent to compare them.
            </p>
          )}
          <Source>{draft.source}. {draft.rules}</Source>
        </>
      )}
    </Card>
  );
}

export function PlanningPage() {
  const { data, error, loading } = useData<Any>("/api/modules/planning/summary");
  const [creating, setCreating] = useState(false);
  return (
    <>
      <section className="hero">
        <div>
          <p className="hero-tag">Module 2 · from demand to work orders</p>
          <h1>Production Planning</h1>
          <p className="hero-sub">
            Production plans per period. A confirmed plan is a commitment: its customer orders move to Planned and its work orders go to the shop floor.
          </p>
          <div className="row">
            <button className="primary big" onClick={() => setCreating(true)}>+ New plan</button>
            <Link to="/capacity" className="button">Capacity plan</Link>
          </div>
        </div>
      </section>
      <ReplanNotice />
      <PlanList onCreate={() => setCreating(true)} />
      <AgentDraft />
      <Status loading={loading} error={error} />
      {data && <SolverViews data={data} />}
      <AskAgent module="planning" placeholder="Can we absorb FB-ARKA +40% next month? Compare the options." />
      <CreatePlanDialog open={creating} month={data?.current?.month ?? null} onClose={() => setCreating(false)} />
    </>
  );
}

export default PlanningPage;
