/** Module pages with thin-to-medium depth: Purchasing, Delivery. Forecast, Planning and Material have their own files. */

import { AskAgent } from "../components/agents";
import { Badge, Card, Kpi, Source, Status, Table, num, pct } from "../components/ui";
import { formatIdr, formatIdrCompact } from "../lib/format";
import { useData } from "../lib/store";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

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
