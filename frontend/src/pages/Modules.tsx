/** Module pages with thin-to-medium depth: Planning, Material, Purchasing, Delivery. */

import { useState } from "react";
import { AskAgent } from "../components/agents";
import { Bars, Line } from "../components/charts";
import { Badge, Card, Kpi, Source, Status, Table, num, pct } from "../components/ui";
import { formatIdr, formatIdrCompact } from "../lib/format";
import { useData } from "../lib/store";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

export function PlanningPage() {
  const { data, error, loading } = useData<Any>("/api/modules/planning/summary");
  const [whatIf, setWhatIf] = useState(false);
  const wi = useData<Any>(whatIf ? "/api/modules/planning/what-if" : null);
  const cur = data?.current;
  const g = cur?.groups?.[0];
  return (
    <>
      <h1>Production Planning</h1>
      <Status loading={loading} error={error} />
      {data && (
        <>
          <div className="kpis">
            <Kpi label="Bottleneck" value={cur.bottleneck_machine} sub={`load ${pct(cur.bottleneck_load_pct)}`} tone="bad" />
            <Kpi label="3,500 t group gap" value={pct(g.capacity_gap_pct)} sub={g.status} tone={g.capacity_gap_pct > 0 ? "bad" : "good"} />
            <Kpi label="Demand change applied" value={Object.keys(cur.demand_change_pct).length ? Object.entries(cur.demand_change_pct).map(([k, v]) => `${k} +${v}%`).join(", ") : "none"} />
            <Kpi label="Working days (month)" value={cur.working_days} />
          </div>
          <Card title={`Molding load per machine, ${cur.month.slice(0, 7)}`}>
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
          <AskAgent module="planning" placeholder="Can we absorb FB-ARKA +40% next month? Compare the options." />
        </>
      )}
    </>
  );
}

export function MaterialPage() {
  const { data, error, loading } = useData<Any>("/api/modules/material/summary");
  const r = data?.resin;
  return (
    <>
      <h1>Material Supply</h1>
      <Status loading={loading} error={error} />
      {data && (
        <>
          <div className="kpis">
            <Kpi label="PP-EPDM-T20 stockout" value={r.stockout_date ?? "none in 21 days"} sub={r.days_to_stockout != null ? `in ${r.days_to_stockout} days` : "covered"} tone={r.days_to_stockout != null && r.days_to_stockout <= 10 ? "bad" : "good"} />
            <Kpi label="On hand" value={`${num(r.on_hand_qty)} kg`} />
            <Kpi label="Use per working day" value={`${num(r.actual_rate_per_workday)} kg`} sub={`BOM ${num(r.bom_rate_per_workday)} kg`} />
            <Kpi label="Above BOM" value={pct(r.overconsumption_vs_bom_pct)} sub="short-shot scrap" tone="warn" />
          </div>
          <Card title="Resin PP-EPDM-T20 projection (kg)">
            <Line label="Resin projection" history={r.projection.map((p: Any) => ({ x: p.date, y: Math.max(0, p.projected_qty) }))} />
            {r.open_receipts.length > 0 && <p className="small">Incoming: {r.open_receipts.map((x: Any) => `${num(x.qty)} kg on ${x.expected_date}`).join("; ")}</p>}
            <Source>{r.source}</Source>
          </Card>
          <Card title="Requirements this month (plan × BOM)">
            <Table
              cols={[{ key: "m", label: "Material" }, { key: "req", label: "Required", align: "right" }, { key: "on", label: "On hand", align: "right" }, { key: "cov", label: "Coverage", align: "right" }]}
              rows={data.requirements.requirements.map((x: Any) => ({ m: `${x.material_id} · ${x.material_name}`, req: `${num(x.required_qty)} ${x.unit}`, on: x.on_hand_qty == null ? "–" : `${num(x.on_hand_qty)} ${x.unit}`, cov: x.coverage_pct == null ? "–" : <Badge tone={x.coverage_pct < 50 ? "bad" : "neutral"}>{pct(x.coverage_pct)}</Badge> }))}
            />
            <Source>{data.requirements.source}</Source>
          </Card>
        </>
      )}
    </>
  );
}

export function PurchasingPage() {
  const { data, error, loading } = useData<Any>("/api/modules/purchasing/summary");
  return (
    <>
      <h1>Purchasing</h1>
      <Status loading={loading} error={error} />
      {data && (
        <>
          <Card title="Vendor scorecard (90 days)">
            <Table
              cols={[{ key: "v", label: "Vendor" }, { key: "c", label: "Category" }, { key: "n", label: "POs", align: "right" }, { key: "ot", label: "On time", align: "right" }, { key: "d", label: "Avg delay", align: "right" }, { key: "lt", label: "Lead time (quoted / actual)", align: "right" }]}
              rows={data.scorecard.vendors.map((v: Any) => ({ v: v.vendor_name, c: v.category, n: v.po_count, ot: <Badge tone={v.on_time_pct < 50 ? "bad" : v.on_time_pct < 85 ? "warn" : "good"}>{pct(v.on_time_pct)}</Badge>, d: `${v.avg_delay_days} d`, lt: `${v.quoted_lead_time_days} / ${v.effective_lead_time_days} d` }))}
            />
            <Source>{data.scorecard.source}</Source>
          </Card>
          <Card title={`Urgent resin PO: need by ${data.resin_po.need_by ?? "–"}`}>
            <Table
              cols={[{ key: "v", label: "Vendor" }, { key: "a", label: "Expected arrival" }, { key: "ok", label: "In time?" }, { key: "p", label: "PO value", align: "right" }]}
              rows={data.resin_po.options.map((o: Any) => ({ v: o.vendor_name, a: `${o.expected_arrival} (+${o.expected_delay_days} d delay)`, ok: o.arrives_before_need ? <Badge tone="good">yes</Badge> : <Badge tone="bad">no</Badge>, p: formatIdr(o.po_value_idr) }))}
            />
            <p>Recommended: <strong>{data.resin_po.recommended_vendor}</strong> (premium {formatIdr(data.resin_po.premium_vs_cheapest_idr)}).</p>
            {data.placed.length > 0 && <p><Badge tone="good">approved</Badge> {data.placed.map((p: Any) => `${num(p.qty)} kg arriving ${p.expected_date}`).join("; ")}</p>}
            <p className="small">Inbox: <a href="/api/documents/vendor-email-andalas.txt" target="_blank" rel="noreferrer">email from PT Andalas Polimindo</a></p>
          </Card>
          <AskAgent module="purchasing" placeholder="Which vendor should supply the next PP-EPDM-T20 lot, and why?" />
        </>
      )}
    </>
  );
}

export function DeliveryPage() {
  const { data, error, loading } = useData<Any>("/api/modules/delivery/summary");
  const r = data?.risk;
  return (
    <>
      <h1>Delivery</h1>
      <Status loading={loading} error={error} />
      {data && (
        <>
          <div className="kpis">
            <Kpi label="Orders at risk" value={r.count} sub={data.baseline_count !== r.count ? `was ${data.baseline_count} before approved actions` : `on ${r.machines_involved.join(", ") || "–"}`} tone={r.count ? "bad" : "good"} />
            <Kpi label="Penalty exposure" value={formatIdrCompact(r.total_penalty_idr)} tone={r.total_penalty_idr ? "bad" : "good"} />
            <Kpi label="SKU × colour below 6 h target" value={r.below_stock_target.length} tone={r.below_stock_target.length ? "warn" : "good"} />
            <Kpi label="Revised ship dates" value={Object.keys(data.revised_ship_dates).length} />
          </div>
          <Card title="Orders at risk (ATP)">
            <Table
              empty="No order at risk."
              cols={[{ key: "o", label: "Order" }, { key: "c", label: "Customer" }, { key: "q", label: "Qty", align: "right" }, { key: "req", label: "Requested" }, { key: "est", label: "Est. completion" }, { key: "pen", label: "Penalty", align: "right" }, { key: "rev", label: "Revised" }]}
              rows={r.orders_at_risk.map((o: Any) => ({ o: o.order_id, c: o.customer_name, q: `${num(o.order_qty)} ${o.sku}`, req: o.requested_delivery_date, est: <Badge tone="bad">{o.estimated_completion} (+{o.days_late} d)</Badge>, pen: formatIdr(o.penalty_idr), rev: o.revised_ship_date ?? "–" }))}
            />
            <Source>{r.source}</Source>
          </Card>
          <Card title="Stock in hours of customer use (target 6 h)">
            <Table
              empty="All SKU × colour at or above target."
              cols={[{ key: "s", label: "SKU × colour" }, { key: "h", label: "Stock (h)", align: "right" }, { key: "g", label: "GAP (h)", align: "right" }]}
              rows={r.below_stock_target.map((x: Any) => ({ s: `${x.sku} ${x.color}`, h: x.stock_hours, g: <span className="neg">{x.gap_hours}</span> }))}
            />
          </Card>
          {data.notices.length > 0 && (
            <Card title="Customer notice (approved)">
              <pre className="notice">{data.notices[0].message}</pre>
              <button onClick={() => navigator.clipboard?.writeText(data.notices[0].message)}>Copy</button>
            </Card>
          )}
          <AskAgent module="delivery" placeholder="Which orders will be late and what should we tell the customer?" />
        </>
      )}
    </>
  );
}
