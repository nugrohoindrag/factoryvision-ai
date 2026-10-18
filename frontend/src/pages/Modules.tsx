/** Module pages with thin-to-medium depth: Delivery. Forecast, Planning, Material and Purchasing have their own files. */

import { AskAgent } from "../components/agents";
import { Badge, Card, Kpi, Source, Status, Table, num } from "../components/ui";
import { formatIdr, formatIdrCompact } from "../lib/format";
import { useData } from "../lib/store";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

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
