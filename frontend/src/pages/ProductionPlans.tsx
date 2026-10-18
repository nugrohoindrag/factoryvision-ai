/** Module 2, Production Plans: the list of plans and the dialog that opens a new one; the wizard lives in PlanWizard. */

import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AskAgent } from "../components/agents";
import { Badge, Card, Dialog, Kpi, Status, Table, num } from "../components/ui";
import { ApiError, errorText, post } from "../lib/api";
import { useApp, useData } from "../lib/store";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

export const PLAN_TONE: Record<string, "info" | "warn" | "good" | "neutral" | "bad"> = {
  Draft: "neutral",
  Planning: "info",
  Ready: "info",
  Confirmed: "good",
  "In Execution": "warn",
  Completed: "neutral",
  Cancelled: "bad",
};

export const CAPACITY_TONE: Record<string, "good" | "warn" | "bad"> = {
  "Within Plan": "good",
  "Additional Demand": "warn",
  "Capacity Up Required": "bad",
};

export function PlanStatus({ status }: { status: string }) {
  return <Badge tone={PLAN_TONE[status] ?? "neutral"}>{status}</Badge>;
}

export function CapacityStatus({ status }: { status: string | null | undefined }) {
  if (!status) return <span className="muted">–</span>;
  return <Badge tone={CAPACITY_TONE[status] ?? "neutral"}>{status}</Badge>;
}

const addDays = (iso: string, n: number) => new Date(Date.parse(`${iso}T00:00:00Z`) + n * 86_400_000).toISOString().slice(0, 10);

function NewPlanDialog({ open, asOf, utilization, onClose }: { open: boolean; asOf: string; utilization: number; onClose: () => void }) {
  const { bump } = useApp();
  const navigate = useNavigate();
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [errors, setErrors] = useState<{ field: string; message: string }[]>([]);
  const [banner, setBanner] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const s = start || asOf; // the plant's date, not the browser's: the demo runs on 2027-02-01
  const e = end || (asOf ? addDays(asOf, 6) : "");
  const errorFor = (f: string) => errors.find((x) => x.field === f)?.message;
  const close = () => {
    if (busy) return;
    setStart("");
    setEnd("");
    setErrors([]);
    setBanner(null);
    onClose();
  };
  const create = async () => {
    setBusy(true);
    setErrors([]);
    setBanner(null);
    try {
      const r = await post<Any>("/api/production-plans", { period_start: s, period_end: e });
      bump();
      navigate(`/production-plans/${r.plan.plan_id}`);
    } catch (err) {
      const fields = err instanceof ApiError ? (err.body.detail as Any)?.fields : null;
      if (fields) setErrors(fields);
      else setBanner(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} title="New production plan" onClose={close}>
      <p className="muted small">
        A new plan starts as Draft. Its number follows the month of the period start (PP-2027-02-002 when the month already has a plan). The planning utilization in force
        now ({utilization}%) is kept with the plan.
      </p>
      <div className="form-grid">
        <label>
          Period start
          <input type="date" aria-label="Period start" value={s} onChange={(x) => setStart(x.target.value)} className={errorFor("period_start") ? "invalid" : ""} />
          {errorFor("period_start") && <span className="field-error">{errorFor("period_start")}</span>}
        </label>
        <label>
          Period end
          <input type="date" aria-label="Period end" value={e} onChange={(x) => setEnd(x.target.value)} className={errorFor("period_end") ? "invalid" : ""} />
          {errorFor("period_end") && <span className="field-error">{errorFor("period_end")}</span>}
        </label>
      </div>
      {banner && <p className="error" role="alert">{banner}</p>}
      <div className="dialog-foot">
        <button onClick={close} disabled={busy}>Cancel</button>
        <button className="primary" onClick={create} disabled={busy || !s || !e}>{busy ? "Creating…" : "Create plan"}</button>
      </div>
    </Dialog>
  );
}

/** The planning agent drafts a plan from open orders, forecast and capacity; the draft goes to the Action Center for approval. */
function AgentDraft({ asOf }: { asOf: string }) {
  const { bump } = useApp();
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [forecast, setForecast] = useState(true);
  const [draft, setDraft] = useState<Any>(null);
  const [sent, setSent] = useState<Any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const s = start || asOf;
  const e = end || (asOf ? addDays(asOf, 6) : "");
  const body = { period_start: s, period_end: e, include_forecast: forecast };
  const call = async (path: string) => {
    setBusy(true);
    setErr(null);
    try {
      const r = await post<Any>(path, body);
      if (r.action) {
        setSent(r.action);
        setDraft(r.draft);
        bump();
      } else {
        setDraft(r);
        setSent(null);
      }
    } catch (x) {
      const fields = x instanceof ApiError ? (x.body.detail as Any)?.fields : null;
      setErr(fields?.length ? fields.map((f: Any) => f.message).join(" ") : errorText(x));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Draft with the planning agent" actions={<Badge tone="info">Production Planning agent</Badge>}>
      <p className="muted small">
        The agent takes every open customer order line due in the period, adds forecast not yet ordered, sets priorities (firm orders first, earliest delivery first) and
        checks capacity. A line above capacity is planned at what fits and its gap is shown. The draft goes to the Action Center; after approval it opens as a Draft plan
        you finish in the wizard.
      </p>
      <div className="filters">
        <label>
          Period start
          <input type="date" aria-label="Draft period start" value={s} onChange={(x) => (setStart(x.target.value), setDraft(null), setSent(null))} />
        </label>
        <label>
          Period end
          <input type="date" aria-label="Draft period end" value={e} onChange={(x) => (setEnd(x.target.value), setDraft(null), setSent(null))} />
        </label>
        <label className="row small">
          <input type="checkbox" checked={forecast} onChange={(x) => (setForecast(x.target.checked), setDraft(null), setSent(null))} /> Include forecast not yet ordered
        </label>
      </div>
      <div className="row">
        <button onClick={() => call("/api/production-plans/draft")} disabled={busy || !s || !e}>{busy && !draft ? "Drafting…" : "Prepare draft"}</button>
        {draft && !sent && (
          <button className="primary" onClick={() => call("/api/production-plans/draft/propose")} disabled={busy}>
            Send draft for approval
          </button>
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
            <Kpi label="Forecast not yet ordered" value={num(draft.summary.forecast_qty)} sub="pcs" />
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
              { key: "cap", label: "Capacity", align: "right" },
              { key: "st", label: "Status" },
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
              st: <CapacityStatus status={l.capacity_status} />,
            }))}
          />
          {draft.gaps.length > 0 && (
            <p className="notice-box warn small">
              Over capacity, planned at what fits: {draft.gaps.map((g: Any) => `${g.sku} ${num(g.gap_qty)} pcs short${g.short_of_orders ? ` (${num(g.short_of_orders)} of them firm orders)` : ""}`).join("; ")}.
              Options: an extra shift, a mold move, or less forecast. Ask the agent to compare them.
            </p>
          )}
          <p className="source">Source: {draft.source}. {draft.rules}</p>
        </>
      )}
    </Card>
  );
}

export default function ProductionPlansPage() {
  const [status, setStatus] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [creating, setCreating] = useState(false);
  const qs = new URLSearchParams(Object.entries({ status, period_from: from, period_to: to }).filter(([, v]) => v)).toString();
  const { data, error, loading } = useData<Any>(`/api/production-plans${qs ? `?${qs}` : ""}`);
  const all = useData<Any>("/api/production-plans");
  const counts = useMemo(() => {
    const plans: Any[] = all.data?.plans ?? [];
    return {
      open: plans.filter((p) => ["Draft", "Planning", "Ready"].includes(p.status)).length,
      committed: plans.filter((p) => ["Confirmed", "In Execution"].includes(p.status)).length,
      capUp: plans.filter((p) => ["Draft", "Planning", "Ready"].includes(p.status) && p.capacity_status === "Capacity Up Required").length,
      planned: plans.filter((p) => ["Confirmed", "In Execution"].includes(p.status)).reduce((s, p) => s + p.total_planned_qty, 0),
    };
  }, [all.data]);
  const anyFilter = !!(status || from || to);
  return (
    <>
      <section className="hero">
        <div>
          <p className="hero-tag">Module 2 · Production Planning</p>
          <h1>Production Plans</h1>
          <p className="hero-sub">
            Turn open customer orders and forecast into a production commitment in six steps: Demand, Production Plan, Work Order, Scheduling (OR-Tools CP-SAT), Resource and
            Confirmation. Capacity status is computed by the server; a confirmed plan no longer changes.
          </p>
          <div className="row">
            <button className="primary big" onClick={() => setCreating(true)}>+ New plan</button>
            <Link to="/planning" className="small">Capacity and what-if →</Link>
          </div>
        </div>
      </section>
      {all.data && (
        <div className="kpis">
          <Kpi label="Open plans" value={num(counts.open)} sub="Draft, Planning or Ready" />
          <Kpi label="Committed plans" value={num(counts.committed)} sub={`${num(counts.planned)} bumpers planned`} />
          <Kpi label="Open plans needing capacity" value={num(counts.capUp)} sub="a line is Capacity Up Required" tone={counts.capUp ? "bad" : "good"} />
          <Kpi label="Planning utilization" value={`${all.data.default_utilization_pct}%`} sub="planning capacity = total × utilization" />
        </div>
      )}
      <Card title={data ? `Production plans (${data.plans.length}${anyFilter ? ` of ${data.total}` : ""})` : "Production plans"}>
        <div className="filters">
          <label>
            Status
            <select aria-label="Filter by status" value={status} onChange={(x) => setStatus(x.target.value)}>
              <option value="">All statuses</option>
              {(data?.statuses ?? []).map((s: string) => <option key={s}>{s}</option>)}
            </select>
          </label>
          <label>
            Period from
            <input aria-label="Period from" type="date" value={from} onChange={(x) => setFrom(x.target.value)} />
          </label>
          <label>
            Period to
            <input aria-label="Period to" type="date" value={to} onChange={(x) => setTo(x.target.value)} />
          </label>
        </div>
        {error ? (
          <p className="error" role="alert">Could not load production plans: {error}</p>
        ) : !data && loading ? (
          <Status loading error={null} />
        ) : data && data.plans.length === 0 ? (
          <p className="muted">{anyFilter ? "No plan matches the filter." : "No production plan yet."}</p>
        ) : data ? (
          <div className="table-wrap">
            <table className="order-table">
              <thead>
                <tr>
                  <th>Plan</th>
                  <th>Period</th>
                  <th>Status</th>
                  <th>Capacity</th>
                  <th className="num">Lines</th>
                  <th className="num">Total planned</th>
                  <th>Created by</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.plans.map((p: Any) => (
                  <tr key={p.plan_id}>
                    <td>
                      <strong>{p.plan_number}</strong>
                      {!p.baseline && <> <Badge tone="info">This session</Badge></>}
                    </td>
                    <td>{p.period_start} – {p.period_end}</td>
                    <td><PlanStatus status={p.status} /></td>
                    <td><CapacityStatus status={p.capacity_status} /></td>
                    <td className="num">{p.line_count}</td>
                    <td className="num">{num(p.total_planned_qty)}</td>
                    <td className="small">{p.created_by}</td>
                    <td><Link to={`/production-plans/${p.plan_id}`} className="small">{p.baseline || !["Draft", "Planning", "Ready"].includes(p.status) ? "View" : "Open wizard"}</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        <p className="source">
          Source: plans from the OEM monthly schedule (fv.fact_monthly_plan) and the plans made in this session. Capacity status is the worst of the plan's lines: Within Plan
          &lt; Additional Demand &lt; Capacity Up Required.
        </p>
      </Card>
      <AgentDraft asOf={data?.as_of ?? ""} />
      <AskAgent module="planning" placeholder="Draft a production plan for 8 to 21 Feb from open orders and forecast, and explain the capacity gaps." />
      <NewPlanDialog open={creating} asOf={data?.as_of ?? ""} utilization={data?.default_utilization_pct ?? 80} onClose={() => setCreating(false)} />
    </>
  );
}
