/** Module pages with thin-to-medium depth: Forecast, Material, Purchasing, Delivery. Planning lives in Planning.tsx. */

import { useState } from "react";
import { Link } from "react-router-dom";
import { AskAgent } from "../components/agents";
import { Bars, Line } from "../components/charts";
import { Badge, Card, Kpi, Source, Status, Table, num, pct } from "../components/ui";
import { formatIdr, formatIdrCompact } from "../lib/format";
import { useData } from "../lib/store";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

const signed = (v: number | null | undefined) => (v == null ? "–" : `${v > 0 ? "+" : ""}${pct(v)}`);

export function ForecastPage() {
  const { data, error, loading } = useData<Any>("/api/modules/forecast/summary");
  const [sku, setSku] = useState("FB-ARKA");
  const detail = useData<Any>(data && sku !== "FB-ARKA" ? `/api/forecast/${sku}` : null);
  const cur = sku === "FB-ARKA" ? data : detail.data;
  const f = cur?.focus;
  const vs = cur?.vs_orders;
  const po = data?.customer_po_change_pct?.[sku];
  return (
    <>
      <div className="page-head">
        <h1>Demand Forecast</h1>
        {data && (
          <label className="row small">
            SKU
            <select value={sku} onChange={(e) => setSku(e.target.value)} aria-label="SKU">
              {data.skus.map((s: Any) => <option key={s.sku}>{s.sku}</option>)}
            </select>
          </label>
        )}
      </div>
      <Status loading={loading || detail.loading} error={error ?? detail.error} />
      {f && vs && (
        <>
          <div className="kpis">
            <Kpi label={`${sku} in 6 weeks`} value={signed(f.change_by_final_week_pct)} sub={`vs last 7 days (${num(f.last7_daily_mean)} / day)`} tone={f.change_by_final_week_pct > 20 ? "warn" : undefined} />
            <Kpi label="Peak week" value={`Week ${f.peak_week}`} sub={signed(f.peak_change_pct)} />
            <Kpi label="Customer PO change" value={po ? signed(po) : "none"} sub={po ? "approved revised schedule" : <Link to="/orders">read a customer PO</Link>} tone={po ? "bad" : undefined} />
            <Kpi label="Firm orders cover" value={pct(vs.forecast_total ? (100 * vs.firm_total) / vs.forecast_total : null)} sub={`${num(vs.firm_total)} of ${num(vs.forecast_total)} forecast`} />
          </div>
          <Card title={`${sku} weekly demand: history and forecast`} actions={<Badge tone="info">{f.engine.startsWith("BigQuery") ? "BigQuery ML ARIMA_PLUS" : "Local model (dev)"}</Badge>}>
            <Line
              label={`${sku} weekly demand`}
              history={f.weekly_history.map((w: Any) => ({ x: w.week_start, y: w.total_qty }))}
              forecast={f.weekly_forecast.map((w: Any) => ({ x: w.week_start, y: w.total_qty }))}
            />
            <ul className="muted small">{f.calendar_notes.map((n: string) => <li key={n}>{n}</li>)}</ul>
            <Source>{f.source}; forecast: {f.engine}</Source>
          </Card>
          <Card title="Forecast vs customer orders">
            <Table
              cols={[
                { key: "wk", label: "Week" },
                { key: "fc", label: "Forecast", align: "right" },
                { key: "firm", label: "Firm orders", align: "right" },
                { key: "sch", label: "Customer schedule", align: "right" },
                { key: "rev", label: "Revised by PO", align: "right" },
                { key: "gap", label: "Not yet ordered", align: "right" },
              ]}
              rows={vs.weeks.map((w: Any) => ({
                wk: `W${w.week} · ${w.week_start}`,
                fc: <strong>{num(w.forecast_qty)}</strong>,
                firm: `${num(w.firm_qty)} (${pct(w.firm_coverage_pct, 0)})`,
                sch: num(w.schedule_qty),
                rev: w.revised_schedule_qty != null && w.revised_schedule_qty !== w.schedule_qty ? <Badge tone="warn">{num(w.revised_schedule_qty)}</Badge> : num(w.revised_schedule_qty),
                gap: num(w.uncovered_qty),
              }))}
            />
            <p className="muted small">{vs.note}</p>
            <Source>{vs.source}</Source>
          </Card>
          <Card title="All SKUs: change by week 6">
            <Bars items={data.skus.map((s: Any) => ({ label: s.sku, value: s.change_by_final_week_pct, tone: s.change_by_final_week_pct > 20 ? "warn" : undefined }))} unit="%" />
          </Card>
          <AskAgent module="forecast" placeholder={`Why is ${sku} demand changing, and what does the OEM-A PO change?`} />
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
