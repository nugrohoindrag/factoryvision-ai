/** Module 0, Customer Orders: the order book and customer PO intake that start the Order-to-Delivery chain. */

import { useState } from "react";
import { Link } from "react-router-dom";
import { Bars } from "../components/charts";
import { Badge, Card, Kpi, Source, Status, Table, num, pct } from "../components/ui";
import { errorText, post, postForm } from "../lib/api";
import { useApp, useData } from "../lib/store";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

const DEMO_PO = "customer-po-oem-a.pdf";
const PAGE = 12;
const STATUS_TONE: Record<string, "info" | "warn" | "good" | "neutral"> = { Received: "info", Planned: "info", "In Production": "warn", Produced: "warn", "Ready to Ship": "good", Shipped: "good", Completed: "neutral" };

export function StatusFlow({ counts, statuses }: { counts: Record<string, number>; statuses: string[] }) {
  return (
    <ol className="flow" aria-label="Customer order status flow">
      {statuses.filter((s) => s !== "Cancelled").map((s) => (
        <li key={s} className={counts[s] ? "has" : ""}>
          <span className="flow-count">{num(counts[s] ?? 0)}</span>
          <span className="flow-label">{s}</span>
        </li>
      ))}
    </ol>
  );
}

/** DF-2: read a customer PO (demo PDF or an upload), check it against master data, send the change for approval. */
export function PoIntake() {
  const { live, mode, bump } = useApp();
  const [file, setFile] = useState<File | null>(null);
  const [res, setRes] = useState<Any>(null);
  const [sent, setSent] = useState<Any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const ai = live && !!mode?.ai_enabled;
  const read = async (upload: File | null) => {
    setBusy(true);
    setErr(null);
    setSent(null);
    const f = new FormData();
    if (upload) f.append("file", upload);
    else f.append("name", DEMO_PO);
    f.append("replay", ai ? "false" : "true");
    try {
      setRes(await postForm("/api/orders/po/read", f));
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const propose = async () => {
    setBusy(true);
    setErr(null);
    try {
      setSent(await post("/api/orders/po/propose", res.extracted));
      bump();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const c = res?.check;
  return (
    <Card title="Customer PO intake" actions={<Badge tone="info">Document &amp; Photo Extractor</Badge>}>
      <div className="row">
        <button className="primary" onClick={() => read(null)} disabled={busy}>
          {busy && !file ? "Reading…" : `Read the OEM-A revised schedule${ai ? "" : " (Replay)"}`}
        </button>
        <a href={`/api/documents/${DEMO_PO}`} target="_blank" rel="noreferrer" className="small">Open the PDF</a>
        <span className="muted small">or</span>
        <input type="file" accept="application/pdf" aria-label="Upload a customer PO (PDF)" onChange={(e) => setFile(e.target.files?.[0] ?? null)} disabled={!ai} />
        <button onClick={() => file && read(file)} disabled={!ai || !file || busy}>Read upload</button>
      </div>
      {!ai && <p className="muted small">Replay shows the recorded extraction of the demo PDF. Uploading your own PDF needs Live with AI (one Gemini call).</p>}
      {err && <p className="error" role="alert">{err}</p>}
      {c && (
        <div className="po">
          <div className="po-head">
            <div><span className="muted small">Customer</span><strong>{c.customer_name}</strong></div>
            <div><span className="muted small">PO</span><strong>{c.po_number ?? "–"}</strong></div>
            <div><span className="muted small">Issued</span><strong>{c.issue_date ?? "–"}</strong></div>
            <div><span className="muted small">Read by</span><strong>{res.engine}</strong></div>
          </div>
          <Table
            cols={[
              { key: "sku", label: "SKU" },
              { key: "month", label: "Month" },
              { key: "prev", label: "Previous", align: "right" },
              { key: "qty", label: "Revised", align: "right" },
              { key: "chg", label: "Change", align: "right" },
              { key: "day", label: "Per working day", align: "right" },
              { key: "chk", label: "On file" },
            ]}
            rows={c.lines.map((l: Any) => ({
              sku: <strong>{l.sku}</strong>,
              month: l.month,
              prev: num(l.previous_qty),
              qty: num(l.qty),
              chg: l.change_pct ? <Badge tone={l.change_pct > 0 ? "warn" : "info"}>{l.change_pct > 0 ? "+" : ""}{pct(l.change_pct)}</Badge> : <span className="muted">no change</span>,
              day: num(l.per_working_day),
              chk: l.matches_on_file == null ? <span className="muted">–</span> : l.matches_on_file ? <Badge tone="good">matches {num(l.on_file_qty)}</Badge> : <Badge tone="bad">on file {num(l.on_file_qty)}</Badge>,
            }))}
          />
          {c.warnings.length > 0 && <ul className="error small">{c.warnings.map((w: string) => <li key={w}>{w}</li>)}</ul>}
          {c.notes && <p className="small">Note on the PO: {c.notes}</p>}
          <div className="row">
            <button className="primary" onClick={propose} disabled={busy || !c.changed.length || !!sent}>
              {c.changed.length ? `Send ${c.changed.length} order change${c.changed.length > 1 ? "s" : ""} for approval` : "Nothing to change"}
            </button>
            {sent && <span className="small">Pending in the <Link to="/actions">Action Center</Link>. After approval, Forecast, Planning, Material and Purchasing use the revised demand.</span>}
          </div>
          <Source>{c.source}. Quantities are re-checked on the server; the model only reads the document.</Source>
        </div>
      )}
    </Card>
  );
}

export default function OrdersPage() {
  const { data, error, loading } = useData<Any>("/api/modules/orders/summary");
  const [sku, setSku] = useState("");
  const [status, setStatus] = useState("");
  const [all, setAll] = useState(false);
  const b = data?.book;
  const rows = (b?.open_orders ?? []).filter((o: Any) => (!sku || o.sku === sku) && (!status || o.order_status === status));
  const skus = [...new Set<string>((b?.open_orders ?? []).map((o: Any) => o.sku))].sort();
  const applied = data?.customer_pos ?? [];
  return (
    <>
      <section className="hero">
        <div>
          <p className="hero-tag">Module 0 · start of the Order-to-Delivery chain</p>
          <h1>Customer Orders</h1>
          <p className="hero-sub">Firm orders and revised customer schedules. A PO change approved here flows into Forecast, Planning, Material and Purchasing.</p>
        </div>
      </section>
      <Status loading={loading} error={error} />
      {b && (
        <>
          <div className="kpis">
            <Kpi label="Open orders" value={num(b.open_count)} sub={`${num(b.open_qty)} bumpers`} />
            <Kpi label="Due this week" value={num(b.due_this_week)} sub={`week of ${b.as_of}`} />
            <Kpi label="On-time delivery (30 days)" value={pct(b.on_time_delivery_pct)} sub={`${num(b.shipments_30d)} shipments`} tone={b.on_time_delivery_pct != null && b.on_time_delivery_pct < 95 ? "warn" : "good"} />
            <Kpi
              label="Revised customer POs"
              value={applied.length || "none"}
              sub={applied.length ? applied.map((p: Any) => p.po_number).join(", ") : "no PO change approved in this session"}
              tone={applied.length ? "warn" : undefined}
            />
          </div>
          <Card title="Order status (last 30 days and open)">
            <StatusFlow counts={b.status_counts} statuses={b.statuses} />
          </Card>
          <PoIntake />
          <div className="grid2">
            <Card title="Open quantity by customer">
              <Bars items={b.open_by_customer.map((c: Any) => ({ label: c.customer_name, value: c.open_qty }))} unit=" pcs" />
            </Card>
            <Card title="Approved PO revisions">
              {applied.length ? (
                <Table
                  cols={[{ key: "po", label: "PO" }, { key: "sku", label: "SKU" }, { key: "chg", label: "Change", align: "right" }]}
                  rows={applied.flatMap((p: Any) => p.lines.map((l: Any) => ({ po: p.po_number, sku: l.sku, chg: `${l.change_pct > 0 ? "+" : ""}${pct(l.change_pct)} (${num(l.previous_qty)} → ${num(l.qty)})` })))}
                />
              ) : (
                <p className="muted">None yet. Read a customer PO above and approve the change in the Action Center.</p>
              )}
            </Card>
          </div>
          <Card
            title={`Open orders (${rows.length})`}
            actions={
              <div className="row">
                <select aria-label="Filter by SKU" value={sku} onChange={(e) => setSku(e.target.value)}>
                  <option value="">All SKUs</option>
                  {skus.map((s) => <option key={s}>{s}</option>)}
                </select>
                <select aria-label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value)}>
                  <option value="">All statuses</option>
                  {b.statuses.slice(0, 6).map((s: string) => <option key={s}>{s}</option>)}
                </select>
              </div>
            }
          >
            <Table
              cols={[
                { key: "id", label: "Order" },
                { key: "cust", label: "Customer" },
                { key: "sku", label: "SKU" },
                { key: "qty", label: "Qty", align: "right" },
                { key: "req", label: "Requested delivery" },
                { key: "st", label: "Status" },
              ]}
              rows={(all ? rows : rows.slice(0, PAGE)).map((o: Any) => ({
                id: o.order_id,
                cust: o.customer_name,
                sku: o.sku,
                qty: num(o.order_qty),
                req: o.requested_delivery_date,
                st: <Badge tone={STATUS_TONE[o.order_status] ?? "neutral"}>{o.order_status}</Badge>,
              }))}
              empty="No open orders match the filter."
            />
            {rows.length > PAGE && <button className="ghost small" onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show all ${rows.length}`}</button>}
            <Source>{b.source}</Source>
          </Card>
        </>
      )}
    </>
  );
}
