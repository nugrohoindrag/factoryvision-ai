/**
 * Module 4, Material Supply: is the material there when the plan needs it, and what to buy when it is not.
 *
 * Four tabs. Readiness checks each production plan's BOM explosion against stock (Available = On Hand − Reserved +
 * Incoming, where a receipt counts only if it arrives by the need date). MRP nets the horizon's gross requirement
 * and turns a net shortage into a purchase proposal in the Action Center. Inventory shows stock per warehouse with
 * reservations, consumption against the BOM and the ledger. BOM shows what one SKU in one colour is made of.
 * Every number comes from the API; the page only arranges it.
 */

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AskAgent } from "../components/agents";
import { Line } from "../components/charts";
import { Badge, Card, Kpi, Source, Status, Table, num, pct } from "../components/ui";
import { errorText, get, post } from "../lib/api";
import { colorsFor } from "../lib/colors";
import { formatIdrCompact } from "../lib/format";
import { useApp, useData } from "../lib/store";
import type {
  Bom,
  ConsumptionRow,
  IncomingReceipt,
  InventoryView,
  LedgerRow,
  MaterialCheck,
  MaterialCheckRow,
  MrpResult,
  MrpRun,
  PlanDetail,
  ReadinessOverview,
  ReadinessStatus,
  Reservation,
  Warehouse,
} from "../lib/types";
import { CreatePlanDialog } from "./Planning";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

const TABS = [
  ["readiness", "Readiness"],
  ["mrp", "MRP"],
  ["inventory", "Inventory"],
  ["bom", "BOM"],
] as const;
type Tab = (typeof TABS)[number][0];

const SUBTABS = [
  ["stock", "Stock"],
  ["reservations", "Reservations"],
  ["consumption", "Consumption"],
  ["transactions", "Transactions"],
] as const;
type SubTab = (typeof SUBTABS)[number][0];

const GROUPS = ["Resin", "Masterbatch", "Primer", "Basecoat", "Clear", "Hardener", "Thinner", "Component", "Packaging"];
const PROCESS_ORDER = ["Molding", "Painting", "Assembly", "Packing"];

/** Material quantities are often fractions of a kilogram, so keep two decimals (num() rounds to whole units). */
export const qty = (v: number | null | undefined) => (v == null ? "–" : v.toLocaleString("en-US", { maximumFractionDigits: 2 }));
const dt = (iso: string | null | undefined) => (iso ? iso.slice(0, 16).replace("T", " ") : "–");

/** A list endpoint may answer with the array itself or wrap it; accept both so the page does not depend on the wrapper. */
function listOf<T>(d: Any, ...keys: string[]): T[] {
  if (Array.isArray(d)) return d as T[];
  for (const k of keys) if (Array.isArray(d?.[k])) return d[k] as T[];
  return [];
}

const STATUS_TONE: Record<string, "good" | "warn" | "bad" | "neutral"> = { READY: "good", PARTIAL: "warn", SHORTAGE: "bad", NOT_CHECKED: "neutral" };
const STATUS_LABEL: Record<string, string> = { READY: "Ready", PARTIAL: "Partial", SHORTAGE: "Shortage", NOT_CHECKED: "Not checked" };

export function ReadinessBadge({ status }: { status: ReadinessStatus | string | null | undefined }) {
  const s = status ?? "NOT_CHECKED";
  return <Badge tone={STATUS_TONE[s] ?? "neutral"}>{STATUS_LABEL[s] ?? s}</Badge>;
}

const CONSUMPTION_TONE: Record<string, "good" | "warn" | "bad"> = { NORMAL: "good", OVER_CONSUMPTION: "bad", UNDER_CONSUMPTION: "warn" };
const CONSUMPTION_LABEL: Record<string, string> = { NORMAL: "Normal", OVER_CONSUMPTION: "Over", UNDER_CONSUMPTION: "Under" };

function Empty({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      <div className="muted">{children}</div>
    </div>
  );
}

function LoadError({ what, error }: { what: string; error: string }) {
  const { bump } = useApp();
  return (
    <div className="notice-box bad" role="alert">
      Could not load {what}: {error} <button className="small" onClick={bump}>Retry</button>
    </div>
  );
}

function Bar({ value, status }: { value: number | null | undefined; status: string }) {
  if (value == null) return <span className="muted">–</span>;
  const color = status === "READY" ? "var(--good)" : status === "PARTIAL" ? "var(--warn)" : "var(--bad)";
  return (
    <div className="progress">
      <span className="bar"><span style={{ width: `${Math.min(100, value)}%`, background: color }} /></span>
      <span className="small">{pct(value, 0)}</span>
    </div>
  );
}

/* ---------------- Readiness ---------------- */

/** One row per material: where Available comes from, which receipts count, and what is short. */
export function MaterialRows({ rows }: { rows: MaterialCheckRow[] }) {
  if (!rows.length) return <p className="muted">No material requirement: the plan has no lines with an active BOM.</p>;
  return (
    <div className="table-wrap">
      <table aria-label="Material requirements">
        <thead>
          <tr>
            <th>Material</th>
            <th>Colour</th>
            <th className="right">Required</th>
            <th>Need date</th>
            <th className="right">On hand</th>
            <th className="right">Reserved</th>
            <th>Incoming</th>
            <th className="right">Available</th>
            <th className="right">Shortage</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => (
            <tr key={`${m.material_id}-${m.color ?? ""}`}>
              <td>
                <strong>{m.material_id}</strong> <span className="muted small">{m.material_name}</span>
                <div className="muted small">{m.material_group}</div>
              </td>
              <td>{m.color ?? "–"}</td>
              <td className="right">{qty(m.required_qty)} {m.unit}</td>
              <td>{m.required_date ?? "–"}</td>
              <td className="right">{qty(m.on_hand_qty)}</td>
              <td className="right">{qty(m.reserved_qty)}</td>
              <td>
                {(m.incoming ?? []).length === 0 ? (
                  <span className="muted">–</span>
                ) : (
                  <ul className="doc-list small">
                    {m.incoming.map((r) => (
                      <li key={r.po_id}>
                        {qty(r.qty)} on {r.expected_date} <span className="muted">({r.po_id})</span>
                        {!r.counted && <> <Badge tone="warn">arrives after need date</Badge></>}
                      </li>
                    ))}
                  </ul>
                )}
                {m.incoming_counted_qty !== m.incoming_qty && <div className="muted small">counted {qty(m.incoming_counted_qty)} of {qty(m.incoming_qty)}</div>}
              </td>
              <td className="right"><strong>{qty(m.available_qty)}</strong></td>
              <td className={`right ${m.shortage_qty > 0 ? "neg" : "muted"}`}>{qty(m.shortage_qty)}</td>
              <td>
                <ReadinessBadge status={m.status} />
                {m.at_risk && <> <Badge tone="warn">At risk</Badge></>}
                {m.note && <div className="muted small">{m.note}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CheckHeader({ check }: { check: MaterialCheck }) {
  return (
    <p className="small">
      <ReadinessBadge status={check.status} /> {check.readiness_pct != null && <><strong>{pct(check.readiness_pct, 0)}</strong> of materials ready · </>}
      {check.shortages?.length ?? 0} short · earliest need {check.required_date ?? "–"} · {check.checked_at ? `checked ${dt(check.checked_at)}` : "not checked yet"}
    </p>
  );
}

/** MR-R21: one work order's own explosion, needed on its planned start, after the work orders before it. */
function WorkOrderCheck({ planId }: { planId: string }) {
  const { data: plan } = useData<PlanDetail>(`/api/plans/${encodeURIComponent(planId)}`);
  const [wo, setWo] = useState("");
  const [check, setCheck] = useState<MaterialCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const wos = plan?.work_orders ?? [];
  if (!wos.length) return null;
  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      setCheck(await post<MaterialCheck>(`/api/materials/readiness/plans/${encodeURIComponent(planId)}/work-orders/${encodeURIComponent(wo)}`));
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div>
      <h3>Per work order</h3>
      <div className="filters">
        <label>
          Work order
          <select aria-label="Work order to check" value={wo} onChange={(e) => (setWo(e.target.value), setCheck(null))}>
            <option value="">Choose a work order</option>
            {wos.map((w) => <option key={w.wo_id} value={w.wo_id}>{w.wo_id} · {w.process} · {w.planned_start?.slice(0, 10) ?? "unscheduled"}</option>)}
          </select>
        </label>
        <button onClick={run} disabled={!wo || busy}>{busy ? "Checking…" : "Check work order"}</button>
      </div>
      {err && <p className="error" role="alert">{err}</p>}
      {check && (
        <>
          <CheckHeader check={check} />
          <MaterialRows rows={check.materials ?? []} />
        </>
      )}
    </div>
  );
}

function PlanCheck({ planId, label }: { planId: string; label: string }) {
  const { version } = useApp();
  const [check, setCheck] = useState<MaterialCheck | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const path = `/api/materials/readiness/plans/${encodeURIComponent(planId)}`;
  useEffect(() => {
    let alive = true;
    setLoading(true);
    get<MaterialCheck>(path)
      .then((c) => alive && (setCheck(c), setErr(null)))
      .catch((e) => alive && setErr(errorText(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [path, version]);
  const checkNow = async () => {
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
  const shown = check && check.plan_id === planId ? check : null;
  return (
    <Card title={`Material requirements: ${shown?.plan_number ?? label}`} actions={<button className="primary" onClick={checkNow} disabled={busy}>{busy ? "Checking…" : "Check now"}</button>}>
      <p className="formula"><strong>Available = On Hand − Reserved + Incoming</strong> <span className="muted small">· Shortage = max(Required − Available, 0). Incoming counts only when it arrives on or before the need date.</span></p>
      {err && <div className="notice-box bad" role="alert">{err} <button className="small" onClick={checkNow}>Retry</button></div>}
      {loading && !shown && <p className="muted">Loading the last check…</p>}
      {shown && (
        <>
          <CheckHeader check={shown} />
          {shown.status === "NOT_CHECKED" ? (
            <Empty title="Not checked yet">Press Check now to explode the plan's BOM and compare it with stock.</Empty>
          ) : (
            <MaterialRows rows={shown.materials ?? []} />
          )}
        </>
      )}
      {!planId.startsWith("OEM-") && <WorkOrderCheck planId={planId} />}
      <p className="muted small">Ready: no shortage · Partial: some available, not enough · Shortage: nothing available. At risk: covered, but stock after use falls below safety stock.</p>
    </Card>
  );
}

/** MR-R46: the resin story that predates the readiness check: daily projection and this month's plan × BOM. */
function ResinProjection() {
  const { data, error, loading } = useData<Any>("/api/modules/material/summary");
  const r = data?.resin;
  return (
    <>
      <Status loading={loading} error={error} />
      {r && (
        <>
          <div className="kpis">
            <Kpi label="PP-EPDM-T20 stockout" value={r.stockout_date ?? "none in 21 days"} sub={r.days_to_stockout != null ? `in ${r.days_to_stockout} days` : "covered"} tone={r.days_to_stockout != null && r.days_to_stockout <= 10 ? "bad" : "good"} />
            <Kpi label="On hand" value={`${num(r.on_hand_qty)} kg`} />
            <Kpi label="Use per working day" value={`${num(r.actual_rate_per_workday)} kg`} sub={`BOM ${num(r.bom_rate_per_workday)} kg`} />
            <Kpi label="Above BOM" value={pct(r.overconsumption_vs_bom_pct)} sub="short-shot scrap" tone="warn" />
          </div>
          <Card title="Resin PP-EPDM-T20 projection (kg)">
            <Line label="Resin projection" history={(r.projection ?? []).map((p: Any) => ({ x: p.date, y: Math.max(0, p.projected_qty) }))} />
            {(r.open_receipts ?? []).length > 0 && <p className="small">Incoming: {r.open_receipts.map((x: Any) => `${num(x.qty)} kg on ${x.expected_date}`).join("; ")}</p>}
            <Source>{r.source}</Source>
          </Card>
        </>
      )}
      {data?.requirements && (
        <Card title="Requirements this month (plan × BOM)">
          <Table
            cols={[{ key: "m", label: "Material" }, { key: "req", label: "Required", align: "right" }, { key: "on", label: "On hand", align: "right" }, { key: "cov", label: "Coverage", align: "right" }]}
            rows={(data.requirements.requirements ?? []).map((x: Any) => ({
              m: `${x.material_id} · ${x.material_name}`,
              req: `${num(x.required_qty)} ${x.unit}`,
              on: x.on_hand_qty == null ? "–" : `${num(x.on_hand_qty)} ${x.unit}`,
              cov: x.coverage_pct == null ? "–" : <Badge tone={x.coverage_pct < 50 ? "bad" : "neutral"}>{pct(x.coverage_pct)}</Badge>,
            }))}
          />
          <Source>{data.requirements.source}</Source>
        </Card>
      )}
    </>
  );
}

function ReadinessTab({ planParam, onPlan }: { planParam: string | null; onPlan: (id: string) => void }) {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [only, setOnly] = useState(false);
  const [creating, setCreating] = useState(false);
  const badRange = !!from && !!to && to < from;
  const qs = new URLSearchParams(Object.entries({ from: badRange ? "" : from, to: badRange ? "" : to, only_problems: only ? "true" : "" }).filter(([, v]) => v)).toString();
  const { data, error, loading } = useData<ReadinessOverview>(`/api/materials/readiness${qs ? `?${qs}` : ""}`);
  const plans = (data?.plans ?? []).filter((p) => !only || p.status !== "READY");
  const selected = plans.find((p) => p.plan_id === planParam) ?? plans[0] ?? null;
  const k = data?.kpis;
  return (
    <>
      {k && (
        <div className="kpis">
          <Kpi label="Material readiness" value={pct(k.readiness_pct, 0)} sub="materials ready across plans" tone={k.readiness_pct == null ? undefined : k.readiness_pct >= 100 ? "good" : "warn"} />
          <Kpi label="Plans monitored" value={num(k.plans_monitored)} sub={`${data?.from} → ${data?.to}`} />
          <Kpi label="Plans short" value={num(k.plans_short)} sub="cannot run in full" tone={k.plans_short ? "bad" : "good"} />
          <Kpi label="Materials short" value={num(k.materials_short)} sub="requirement rows not covered" tone={k.materials_short ? "bad" : "good"} />
        </div>
      )}
      <Card title="Material readiness per production plan">
        <div className="filters">
          <label>
            From
            <input type="date" aria-label="Readiness from" value={from || data?.from || ""} onChange={(e) => setFrom(e.target.value)} />
          </label>
          <label>
            To
            <input type="date" aria-label="Readiness to" value={to || data?.to || ""} onChange={(e) => setTo(e.target.value)} className={badRange ? "invalid" : ""} />
            {badRange && <span className="field-error">The end cannot be before the start.</span>}
          </label>
          <label className="check">
            <input type="checkbox" aria-label="Only problems" checked={only} onChange={(e) => setOnly(e.target.checked)} /> Only problems
          </label>
        </div>
        {error ? (
          <LoadError what="material readiness" error={error} />
        ) : !data && loading ? (
          <p className="muted">Loading material readiness…</p>
        ) : plans.length === 0 ? (
          only && (data?.plans ?? []).length > 0 ? (
            <Empty title="No plan with a problem">Every plan in this range has its material.</Empty>
          ) : (
            <Empty title="No production plan in this range">
              <p>Create a production plan so its material can be checked against stock.</p>
              <button className="primary" onClick={() => setCreating(true)}>+ Create a plan</button>
            </Empty>
          )
        ) : (
          <div className="table-wrap">
            <table aria-label="Plans">
              <thead>
                <tr><th>Plan</th><th>Period</th><th>Status</th><th>Readiness</th><th className="right">Short</th><th>Checked</th><th /></tr>
              </thead>
              <tbody>
                {plans.map((p) => (
                  <tr key={p.plan_id} className={selected?.plan_id === p.plan_id ? "selected" : ""}>
                    <td>
                      <strong>{p.plan_number}</strong> {p.plan_id.startsWith("OEM-") ? <Badge tone="info">OEM</Badge> : p.origin === "agent" ? <Badge tone="info">Agent</Badge> : null}
                    </td>
                    <td>{p.period_start} → {p.period_end}</td>
                    <td><ReadinessBadge status={p.status} /></td>
                    <td><Bar value={p.readiness_pct} status={p.status} /></td>
                    <td className={`right ${p.short_count ? "neg" : ""}`}>{num(p.short_count)}</td>
                    <td className="small">{p.checked_at ? dt(p.checked_at) : <span className="muted">not yet</span>}</td>
                    <td><button className="ghost small" aria-label={`Details of ${p.plan_number}`} onClick={() => onPlan(p.plan_id)}>Details</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {selected && <PlanCheck planId={selected.plan_id} label={selected.plan_number} />}
      <ResinProjection />
      <CreatePlanDialog open={creating} month={data?.from ?? null} onClose={() => setCreating(false)} />
    </>
  );
}

/* ---------------- MRP ---------------- */

function MrpResults({ run }: { run: MrpRun }) {
  const { bump } = useApp();
  const [sent, setSent] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const rows = useMemo(() => [...(run.results ?? [])].sort((a, b) => b.net_qty - a.net_qty), [run]);
  const propose = async (r: MrpResult) => {
    setBusy(r.material_id);
    setErr(null);
    try {
      const res = await post<Any>(`/api/mrp/runs/${encodeURIComponent(run.run_id)}/propose-po`, { material_id: r.material_id });
      const action = res?.action ?? res;
      setSent((s) => ({ ...s, [r.material_id]: action?.action_id ?? "pending" }));
      bump();
    } catch (e) {
      setErr(`${r.material_id}: ${errorText(e)}`);
    } finally {
      setBusy(null);
    }
  };
  return (
    <Card title={`Results ${run.run_id}`}>
      <div className="fields">
        <div className="field"><span className="muted small">Horizon</span><strong>{run.horizon_start} → {run.horizon_end}</strong></div>
        <div className="field"><span className="muted small">Plans</span><strong>{run.plan_ids?.length ? run.plan_ids.join(", ") : "all in horizon"}</strong></div>
        <div className="field"><span className="muted small">Materials computed</span><strong>{num(run.total_materials)}</strong></div>
        <div className="field"><span className="muted small">Short</span><strong className={run.shortage_materials ? "neg" : ""}>{num(run.shortage_materials)}</strong></div>
        <div className="field"><span className="muted small">Run by</span><strong>{run.created_by} · {dt(run.created_at)}</strong></div>
      </div>
      <p className="formula small"><strong>Net = max(Gross − Available, 0)</strong> · Available = On Hand − Reserved + Incoming · order by = need date − lead time. MRP recommends; a purchase happens only after approval in the Action Center.</p>
      {err && <p className="error" role="alert">{err}</p>}
      <Table
        empty="No material requirement in this horizon."
        cols={[
          { key: "m", label: "Material" },
          { key: "c", label: "Colour" },
          { key: "g", label: "Gross", align: "right" },
          { key: "a", label: "Available", align: "right" },
          { key: "n", label: "Net", align: "right" },
          { key: "d", label: "Need date" },
          { key: "o", label: "Order by" },
          { key: "v", label: "Vendor · lead time" },
          { key: "r", label: "Recommendation" },
          { key: "x", label: "" },
        ]}
        rows={rows.map((r) => ({
          m: <><strong>{r.material_id}</strong> <span className="muted small">{r.material_name}</span></>,
          c: r.color ?? "–",
          g: `${qty(r.gross_qty)} ${r.unit}`,
          a: qty(r.available_qty),
          n: <span className={r.net_qty > 0 ? "neg" : "muted"}>{qty(r.net_qty)}</span>,
          d: r.required_date ?? "–",
          o: r.order_by_date ?? "–",
          v: `${r.default_vendor_id ?? "–"} · ${r.lead_time_days ?? "–"} d`,
          r: r.recommendation ?? <span className="muted">covered</span>,
          x:
            r.net_qty > 0 ? (
              sent[r.material_id] ? (
                <span className="small"><Badge tone="info">Pending approval</Badge> <Link to="/actions">Action Center</Link></span>
              ) : (
                <button className="small" aria-label={`Send ${r.material_id} to Purchasing`} disabled={busy !== null} onClick={() => propose(r)}>
                  {busy === r.material_id ? "Sending…" : "Send to Purchasing"}
                </button>
              )
            ) : null,
        }))}
      />
    </Card>
  );
}

function MrpTab() {
  const { bump } = useApp();
  const overview = useData<ReadinessOverview>("/api/materials/readiness");
  const history = useData<Any>("/api/mrp/runs");
  const latest = useData<MrpRun | null>("/api/mrp/runs/latest");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [run, setRun] = useState<MrpRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const badRange = !!start && !!end && end < start;
  const runs = listOf<MrpRun>(history.data, "runs");
  const shown = run ?? (latest.data && (latest.data as Any).run_id ? latest.data : null);
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const runMrp = async () => {
    setBusy(true);
    setErr(null);
    try {
      const body: Record<string, unknown> = {};
      if (start) body.horizon_start = start;
      if (end) body.horizon_end = end;
      if (picked.length) body.plan_ids = picked;
      setRun(await post<MrpRun>("/api/mrp/runs", body));
      bump();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const view = async (id: string) => {
    setErr(null);
    try {
      setRun(await get<MrpRun>(`/api/mrp/runs/${encodeURIComponent(id)}`));
    } catch (e) {
      setErr(errorText(e));
    }
  };
  return (
    <>
      <Card title="Run MRP">
        <p className="muted small">Gross requirement is the BOM explosion of every plan line in the horizon. Leave the dates empty for today → +30 days, and the plans unticked for all plans in the horizon.</p>
        <div className="filters">
          <label>
            Horizon start
            <input type="date" aria-label="Horizon start" value={start} onChange={(e) => setStart(e.target.value)} />
          </label>
          <label>
            Horizon end
            <input type="date" aria-label="Horizon end" value={end} onChange={(e) => setEnd(e.target.value)} className={badRange ? "invalid" : ""} />
            {badRange && <span className="field-error">The horizon end cannot be before the start.</span>}
          </label>
        </div>
        <fieldset className="plan-picks">
          <legend className="small muted">Plans</legend>
          {(overview.data?.plans ?? []).length === 0 && <span className="muted small">No plan in the default horizon.</span>}
          {(overview.data?.plans ?? []).map((p) => (
            <label key={p.plan_id} className="check small">
              <input type="checkbox" checked={picked.includes(p.plan_id)} onChange={() => toggle(p.plan_id)} /> {p.plan_number}
            </label>
          ))}
        </fieldset>
        <div className="row">
          <button className="primary" onClick={runMrp} disabled={busy || badRange}>{busy ? "Running…" : "Run MRP"}</button>
        </div>
        {err && <p className="error" role="alert">{err}</p>}
      </Card>
      {shown ? <MrpResults run={shown} /> : <Card><Empty title="No MRP run yet">Run MRP to net the horizon's requirement against stock.</Empty></Card>}
      <Card title={`Run history (${runs.length})`}>
        {history.error ? (
          <LoadError what="MRP runs" error={history.error} />
        ) : (
          <Table
            empty="No run yet in this session."
            cols={[
              { key: "id", label: "Run" },
              { key: "at", label: "Created" },
              { key: "h", label: "Horizon" },
              { key: "p", label: "Plans" },
              { key: "t", label: "Materials", align: "right" },
              { key: "s", label: "Short", align: "right" },
              { key: "x", label: "" },
            ]}
            rows={runs.map((r) => ({
              id: <strong>{r.run_id}</strong>,
              at: `${dt(r.created_at)} · ${r.created_by}`,
              h: `${r.horizon_start} → ${r.horizon_end}`,
              p: r.plan_ids?.length ? r.plan_ids.join(", ") : "all",
              t: num(r.total_materials),
              s: <span className={r.shortage_materials ? "neg" : ""}>{num(r.shortage_materials)}</span>,
              x: <button className="ghost small" aria-label={`View ${r.run_id}`} onClick={() => view(r.run_id)}>View</button>,
            }))}
          />
        )}
      </Card>
    </>
  );
}

/* ---------------- Inventory ---------------- */

function StockView() {
  const [group, setGroup] = useState("");
  const [wh, setWh] = useState("");
  const qs = new URLSearchParams(Object.entries({ group, warehouse_id: wh }).filter(([, v]) => v)).toString();
  const { data, error, loading } = useData<InventoryView>(`/api/inventory${qs ? `?${qs}` : ""}`);
  const whs = listOf<Warehouse>(useData<Any>("/api/master/warehouses").data, "warehouses");
  const rows = data?.rows ?? [];
  const incoming = useMemo(() => {
    const seen = new Map<string, IncomingReceipt>();
    for (const r of rows) for (const i of r.incoming ?? []) seen.set(`${i.po_id}:${i.material_id}`, i);
    return [...seen.values()].sort((a, b) => String(a.expected_date).localeCompare(String(b.expected_date)));
  }, [rows]);
  const k = data?.kpis;
  return (
    <>
      {k && (
        <div className="kpis">
          <Kpi label="Materials" value={num(k.materials)} />
          <Kpi label="Below reorder point" value={num(k.below_reorder)} tone={k.below_reorder ? "warn" : "good"} />
          <Kpi label="Incoming POs" value={num(k.incoming_pos)} />
          <Kpi label="Stock value" value={formatIdrCompact(k.value_idr)} />
        </div>
      )}
      <Card title="Stock per material and warehouse">
        <div className="filters">
          <label>
            Group
            <select aria-label="Filter by group" value={group} onChange={(e) => setGroup(e.target.value)}>
              <option value="">All groups</option>
              {GROUPS.map((g) => <option key={g}>{g}</option>)}
            </select>
          </label>
          <label>
            Warehouse
            <select aria-label="Filter by warehouse" value={wh} onChange={(e) => setWh(e.target.value)}>
              <option value="">All warehouses</option>
              {whs.map((w) => <option key={w.warehouse_id} value={w.warehouse_id}>{w.warehouse_id}{w.name || w.warehouse_name ? ` — ${w.name ?? w.warehouse_name}` : ""}</option>)}
            </select>
          </label>
        </div>
        <p className="formula small"><strong>Available = On Hand − Reserved + Incoming</strong> (all open receipts). Below reorder: on hand − reserved is under the reorder point.</p>
        {error ? (
          <LoadError what="inventory" error={error} />
        ) : !data && loading ? (
          <p className="muted">Loading inventory…</p>
        ) : (
          <Table
            empty="No stock matches the filter."
            cols={[
              { key: "m", label: "Material" },
              { key: "g", label: "Group" },
              { key: "c", label: "Colour" },
              { key: "w", label: "Warehouse" },
              { key: "on", label: "On hand", align: "right" },
              { key: "re", label: "Reserved", align: "right" },
              { key: "in", label: "Incoming", align: "right" },
              { key: "av", label: "Available", align: "right" },
              { key: "rop", label: "Reorder point", align: "right" },
              { key: "ss", label: "Safety stock", align: "right" },
              { key: "st", label: "" },
            ]}
            rows={rows.map((r) => ({
              m: <><strong>{r.material_id}</strong> <span className="muted small">{r.material_name}</span></>,
              g: r.material_group,
              c: r.color ?? "–",
              w: r.warehouse_id,
              on: `${qty(r.on_hand_qty)} ${r.unit}`,
              re: qty(r.reserved_qty),
              in: qty(r.incoming_qty),
              av: <strong>{qty(r.available_qty)}</strong>,
              rop: qty(r.reorder_point),
              ss: qty(r.safety_stock),
              st: r.below_reorder ? <Badge tone="warn">Below reorder</Badge> : null,
            }))}
          />
        )}
      </Card>
      <Card title={`Incoming (${incoming.length})`}>
        <Table
          empty="No open purchase order."
          cols={[
            { key: "po", label: "PO" },
            { key: "m", label: "Material" },
            { key: "v", label: "Vendor" },
            { key: "q", label: "Qty", align: "right" },
            { key: "p", label: "Promised" },
            { key: "e", label: "Expected" },
            { key: "s", label: "Source" },
          ]}
          rows={incoming.map((i) => ({
            po: <strong>{i.po_id}</strong>,
            m: i.material_id,
            v: i.vendor_id ?? "–",
            q: qty(i.qty),
            p: i.promised_date ?? "–",
            e: <>{i.expected_date ?? "–"}{i.avg_delay_days ? <span className="muted small"> (+{i.avg_delay_days} d vendor delay)</span> : null}</>,
            s: i.source === "approved_po" ? <Badge tone="good">approved here</Badge> : <Badge>open PO</Badge>,
          }))}
        />
        <Source>expected date = vendor promise + that vendor's average delay</Source>
      </Card>
    </>
  );
}

function ReservationsView() {
  const { bump } = useApp();
  const [status, setStatus] = useState("");
  const { data, error } = useData<Any>(`/api/materials/reservations${status ? `?status=${status}` : ""}`);
  const overview = useData<ReadinessOverview>("/api/materials/readiness");
  const [planId, setPlanId] = useState("");
  const [woId, setWoId] = useState("");
  const plan = useData<PlanDetail>(planId ? `/api/plans/${encodeURIComponent(planId)}` : null);
  const [msg, setMsg] = useState<{ text: string; tone: "good" | "bad" } | null>(null);
  const [busy, setBusy] = useState(false);
  const rows = listOf<Reservation>(data, "reservations", "rows");
  const sessionPlans = (overview.data?.plans ?? []).filter((p) => !p.plan_id.startsWith("OEM-"));
  const act = async (fn: () => Promise<unknown>, ok: (r: Any) => string) => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await fn();
      setMsg({ text: ok(r), tone: "good" });
      bump();
    } catch (e) {
      setMsg({ text: errorText(e), tone: "bad" });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Reservations">
      <p className="muted small">A reservation does not move stock; it lowers Available so another work order does not count the same material twice. One reservation per work order: min(required, on hand) per material. Reserving again returns the existing ones.</p>
      <div className="filters">
        <label>
          Plan
          <select aria-label="Plan to reserve for" value={planId} onChange={(e) => (setPlanId(e.target.value), setWoId(""))}>
            <option value="">Choose a plan</option>
            {sessionPlans.map((p) => <option key={p.plan_id} value={p.plan_id}>{p.plan_number}</option>)}
          </select>
        </label>
        <label>
          Work order
          <select aria-label="Work order to reserve for" value={woId} onChange={(e) => setWoId(e.target.value)} disabled={!planId}>
            <option value="">Choose a work order</option>
            {(plan.data?.plan_id === planId ? plan.data.work_orders : []).map((w) => <option key={w.wo_id} value={w.wo_id}>{w.wo_id} · {w.process}</option>)}
          </select>
        </label>
        <button
          className="primary"
          disabled={!planId || !woId || busy}
          onClick={() => act(() => post("/api/materials/reservations", { plan_id: planId, wo_id: woId }), (r) => `${listOf(r, "reservations").length || "The"} reservation(s) for ${woId} are active.`)}
        >
          Reserve material
        </button>
        <label>
          Status
          <select aria-label="Filter reservations by status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option>
            <option>Active</option>
            <option>Released</option>
          </select>
        </label>
      </div>
      {msg && <div className={`notice-box ${msg.tone}`} role={msg.tone === "bad" ? "alert" : "status"}>{msg.text}</div>}
      {error ? (
        <LoadError what="reservations" error={error} />
      ) : (
        <Table
          empty="No reservation in this session."
          cols={[
            { key: "id", label: "Reservation" },
            { key: "wo", label: "Work order" },
            { key: "m", label: "Material" },
            { key: "w", label: "Warehouse" },
            { key: "q", label: "Qty", align: "right" },
            { key: "at", label: "Reserved" },
            { key: "s", label: "Status" },
            { key: "x", label: "" },
          ]}
          rows={rows.map((r) => ({
            id: <strong>{r.reservation_id}</strong>,
            wo: <>{r.wo_id} <span className="muted small">{r.plan_id}</span></>,
            m: <>{r.material_id} {r.material_name && <span className="muted small">{r.material_name}</span>}</>,
            w: r.warehouse_id ?? "–",
            q: `${qty(r.qty)}${r.unit ? ` ${r.unit}` : ""}`,
            at: dt(r.created_at),
            s: r.status === "Active" ? <Badge tone="info">Active</Badge> : <Badge>Released {r.released_at ? dt(r.released_at) : ""}</Badge>,
            x:
              r.status === "Active" ? (
                <button className="ghost small" disabled={busy} aria-label={`Release ${r.reservation_id}`} onClick={() => act(() => post(`/api/materials/reservations/${encodeURIComponent(r.reservation_id)}/release`), () => `${r.reservation_id} released; its quantity is available again.`)}>
                  Release
                </button>
              ) : null,
          }))}
        />
      )}
    </Card>
  );
}

function ConsumptionView() {
  const [wo, setWo] = useState("");
  const { data, error, loading } = useData<Any>(`/api/materials/consumption${wo.trim() ? `?wo_id=${encodeURIComponent(wo.trim())}` : ""}`);
  const rows = listOf<ConsumptionRow>(data, "rows", "materials", "items", "variance");
  const tol = data?.tolerance_pct;
  return (
    <Card title="Consumption against the BOM">
      <p className="muted small">Variance = Actual − Planned, planned from the work order's BOM requirement{tol != null ? `; within ±${tol}% is Normal` : ""}. Resin actuals come from production records.</p>
      <div className="filters">
        <label>
          Work order
          <input aria-label="Filter consumption by work order" placeholder="All work orders" value={wo} onChange={(e) => setWo(e.target.value)} />
        </label>
      </div>
      {error ? (
        <LoadError what="consumption" error={error} />
      ) : !data && loading ? (
        <p className="muted">Loading consumption…</p>
      ) : (
        <Table
          empty="No consumption recorded."
          cols={[
            { key: "wo", label: "Work order" },
            { key: "m", label: "Material" },
            { key: "p", label: "Planned", align: "right" },
            { key: "a", label: "Actual", align: "right" },
            { key: "v", label: "Variance", align: "right" },
            { key: "s", label: "Status" },
          ]}
          rows={rows.map((r) => ({
            wo: r.wo_id ?? (wo || "all"),
            m: <><strong>{r.material_id}</strong> {r.material_name && <span className="muted small">{r.material_name}</span>}</>,
            p: `${qty(r.planned_qty)}${r.unit ? ` ${r.unit}` : ""}`,
            a: qty(r.actual_qty),
            v: <span className={r.variance_qty > 0 ? "neg" : ""}>{r.variance_qty > 0 ? "+" : ""}{qty(r.variance_qty)}{r.variance_pct != null ? ` (${r.variance_pct > 0 ? "+" : ""}${pct(r.variance_pct)})` : ""}</span>,
            s: <Badge tone={CONSUMPTION_TONE[r.status] ?? "neutral"}>{CONSUMPTION_LABEL[r.status] ?? r.status}</Badge>,
          }))}
        />
      )}
      {data?.source && <Source>{data.source}</Source>}
    </Card>
  );
}

function TransactionsView() {
  const [mid, setMid] = useState("");
  const { data, error, loading } = useData<Any>(`/api/inventory/transactions${mid ? `?material_id=${encodeURIComponent(mid)}` : ""}`);
  const materials = listOf<Any>(useData<Any>("/api/master/materials").data, "materials", "rows");
  const rows = listOf<LedgerRow>(data, "rows", "transactions");
  return (
    <Card title="Transaction ledger">
      <p className="muted small">Append-only. Each stock movement is one row with the balance after it, so the history reads without replaying it.</p>
      <div className="filters">
        <label>
          Material
          <select aria-label="Filter ledger by material" value={mid} onChange={(e) => setMid(e.target.value)}>
            <option value="">All materials</option>
            {materials.map((m: Any) => <option key={m.material_id} value={m.material_id}>{m.material_id}{m.material_name ? ` — ${m.material_name}` : ""}</option>)}
          </select>
        </label>
      </div>
      {error ? (
        <LoadError what="transactions" error={error} />
      ) : !data && loading ? (
        <p className="muted">Loading transactions…</p>
      ) : (
        <Table
          empty="No transaction."
          cols={[
            { key: "d", label: "Date" },
            { key: "id", label: "Transaction" },
            { key: "m", label: "Material" },
            { key: "w", label: "Warehouse" },
            { key: "t", label: "Type" },
            { key: "q", label: "Qty", align: "right" },
            { key: "b", label: "Balance after", align: "right" },
            { key: "r", label: "Reference" },
          ]}
          rows={rows.map((r) => ({
            d: dt(r.date),
            id: r.txn_id,
            m: r.material_id,
            w: r.warehouse_id ?? "–",
            t: <Badge tone={r.type === "Receipt" || r.type === "Release" ? "good" : r.type === "Issue" || r.type === "Reserve" ? "warn" : "neutral"}>{r.type}</Badge>,
            q: <span className={r.qty < 0 ? "neg" : "pos"}>{r.qty > 0 ? "+" : ""}{qty(r.qty)}</span>,
            b: <strong>{qty(r.balance_after)}</strong>,
            r: r.ref ?? "–",
          }))}
        />
      )}
    </Card>
  );
}

function InventoryTab({ sub, onSub }: { sub: SubTab; onSub: (s: SubTab) => void }) {
  return (
    <>
      <div className="chips" role="tablist" aria-label="Inventory views">
        {SUBTABS.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={sub === id} className={`chip ${sub === id ? "on" : ""}`} onClick={() => onSub(id)}>{label}</button>
        ))}
      </div>
      {sub === "stock" && <StockView />}
      {sub === "reservations" && <ReservationsView />}
      {sub === "consumption" && <ConsumptionView />}
      {sub === "transactions" && <TransactionsView />}
    </>
  );
}

/* ---------------- BOM ---------------- */

function BomTab() {
  const { data, error, loading } = useData<Any>("/api/master/boms");
  const colorData = useData<Any>("/api/master/colors").data;
  const boms = listOf<Bom>(data, "boms", "rows");
  const skus = useMemo(() => [...new Set(boms.map((b) => b.sku))].sort(), [boms]);
  const [skuPick, setSku] = useState("");
  const [color, setColor] = useState("");
  const sku = skuPick || skus[0] || "";
  const colors = colorsFor(colorData, sku);
  const versions = boms.filter((b) => b.sku === sku).sort((a, b) => String(b.version).localeCompare(String(a.version), undefined, { numeric: true }));
  const active = versions.find((b) => b.status === "Active");
  const items = (active?.items ?? [])
    .filter((i) => !color || !i.color || i.color === color)
    .sort((a, b) => PROCESS_ORDER.indexOf(a.process) - PROCESS_ORDER.indexOf(b.process) || a.material_id.localeCompare(b.material_id));
  if (error) return <LoadError what="bills of material" error={error} />;
  if (!data && loading) return <p className="muted">Loading bills of material…</p>;
  if (!boms.length) return <Card><Empty title="No bill of material">The master data has no BOM yet.</Empty></Card>;
  return (
    <>
      <Card title="Bill of material">
        <div className="filters">
          <label>
            Product
            <select aria-label="BOM product" value={sku} onChange={(e) => (setSku(e.target.value), setColor(""))}>
              {skus.map((s) => <option key={s}>{s}</option>)}
            </select>
          </label>
          <label>
            Colour
            <select aria-label="BOM colour" value={color} onChange={(e) => setColor(e.target.value)}>
              <option value="">All colours</option>
              {colors.map((c) => <option key={c}>{c}</option>)}
            </select>
          </label>
        </div>
        {active ? (
          <>
            <p className="small">
              <strong>{active.bom_id}</strong> version {active.version} <Badge tone="good">Active</Badge> <span className="muted">effective {active.effective_date ?? "–"}</span>
              {color ? ` · showing ${sku} in ${color}: items for every colour plus the ${color} items` : " · every colour variant listed"}
            </p>
            <Table
              empty="No item for this colour."
              cols={[
                { key: "p", label: "Process" },
                { key: "m", label: "Material" },
                { key: "g", label: "Group" },
                { key: "q", label: "Qty per unit", align: "right" },
                { key: "s", label: "Scrap %", align: "right" },
                { key: "e", label: "Incl. scrap", align: "right" },
                { key: "u", label: "Unit" },
                { key: "c", label: "Colour" },
              ]}
              rows={items.map((i) => ({
                p: i.process,
                m: <><strong>{i.material_id}</strong> <span className="muted small">{i.material_name}</span></>,
                g: i.material_group,
                q: qty(i.qty_per_unit),
                s: pct(i.scrap_pct, 0),
                e: qty(Math.round(i.qty_per_unit * (1 + i.scrap_pct / 100) * 1000) / 1000),
                u: i.unit,
                c: i.color ?? <span className="muted">all colours</span>,
              }))}
            />
            <p className="muted small">Required = qty per unit × planned qty × (1 + scrap %). Read-only: the BOM comes from master data.</p>
          </>
        ) : (
          <div className="notice-box bad">{sku} has no active BOM, so its material cannot be checked.</div>
        )}
      </Card>
      <Card title={`Versions of ${sku}`}>
        <Table
          cols={[
            { key: "id", label: "BOM" },
            { key: "v", label: "Version", align: "right" },
            { key: "s", label: "Status" },
            { key: "d", label: "Effective" },
            { key: "n", label: "Items", align: "right" },
          ]}
          rows={versions.map((b) => ({
            id: <strong>{b.bom_id}</strong>,
            v: b.version,
            s: <Badge tone={b.status === "Active" ? "good" : b.status === "Draft" ? "info" : "neutral"}>{b.status}</Badge>,
            d: b.effective_date ?? "–",
            n: num(b.items?.length ?? 0),
          }))}
        />
      </Card>
    </>
  );
}

/* ---------------- Page ---------------- */

export function MaterialPage() {
  const [params, setParams] = useSearchParams();
  const tabParam = params.get("tab");
  const tab: Tab = TABS.some(([id]) => id === tabParam) ? (tabParam as Tab) : "readiness";
  const subParam = params.get("sub");
  const sub: SubTab = SUBTABS.some(([id]) => id === subParam) ? (subParam as SubTab) : "stock";
  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    setParams(next, { replace: true });
  };
  return (
    <>
      <section className="hero">
        <div>
          <p className="hero-tag">Module 4 · between the plan and Purchasing</p>
          <h1>Material Supply</h1>
          <p className="hero-sub">
            Is every material there when the plan needs it? Each plan's BOM is exploded per SKU and colour and compared with stock, reservations and receipts that arrive in
            time. A shortage becomes a purchase proposal that waits for approval.
          </p>
        </div>
      </section>
      <div className="chips" role="tablist" aria-label="Material views">
        {TABS.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={`chip ${tab === id ? "on" : ""}`} onClick={() => set({ tab: id, sub: null })}>{label}</button>
        ))}
      </div>
      {tab === "readiness" && <ReadinessTab planParam={params.get("plan")} onPlan={(id) => set({ plan: id })} />}
      {tab === "mrp" && <MrpTab />}
      {tab === "inventory" && <InventoryTab sub={sub} onSub={(s) => set({ sub: s })} />}
      {tab === "bom" && <BomTab />}
      <AskAgent module="material" placeholder="Which materials are short for the next plan, and which PO would help?" />
    </>
  );
}

export default MaterialPage;
