/** Module 0, Customer Orders: the order book, order entry and customer PO intake that start the Order-to-Delivery chain. */

import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Bars } from "../components/charts";
import { Badge, Card, Kpi, Source, Status, Table, num, pct } from "../components/ui";
import { errorText, post, postForm } from "../lib/api";
import { useApp, useData } from "../lib/store";
import { OrderDetail, OrderLines, StatusPill } from "./OrderDetail";
import { OrderWizard, type OrderMeta } from "./OrderWizard";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

const DEMO_PO = "customer-po-oem-a.pdf";
const PAGE = 25;

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

type SortKey = "order_number" | "customer_name" | "requested_delivery_date";

/** Customer orders as headers with their lines: filter, sort, expand a row for its lines, open one for the detail. */
function OrderList({ onOpen, onMeta }: { onOpen: (n: string) => void; onMeta: (m: OrderMeta) => void }) {
  const [status, setStatus] = useState("");
  const [customer, setCustomer] = useState("");
  const [sku, setSku] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean } | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(0);
  const qs = new URLSearchParams(Object.entries({ status, customer_id: customer, sku, delivery_from: from, delivery_to: to }).filter(([, v]) => v)).toString();
  const { data, error, loading } = useData<Any>(`/api/orders${qs ? `?${qs}` : ""}`);
  const ready = !!data;
  useEffect(() => {
    if (data) onMeta({ as_of: data.as_of, customers: data.customers, products: data.products, channels: data.channels });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);
  const rows: Any[] = useMemo(() => {
    const list = [...(data?.orders ?? [])];
    if (sort) list.sort((a, b) => (a.created_in_session === b.created_in_session ? String(a[sort.key]).localeCompare(String(b[sort.key])) * (sort.desc ? -1 : 1) : a.created_in_session ? -1 : 1));
    return list;
  }, [data, sort]);
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const cur = Math.min(page, pages - 1); // a refetch can shrink the list under the current page
  const toggle = (n: string) => setOpen((s) => {
    const x = new Set(s);
    if (x.has(n)) x.delete(n);
    else x.add(n);
    return x;
  });
  const head = (key: SortKey, label: string) => (
    <th aria-sort={sort?.key === key ? (sort.desc ? "descending" : "ascending") : "none"}>
      <button onClick={() => (setSort((s) => ({ key, desc: s?.key === key ? !s.desc : false })), setPage(0))}>
        {label} {sort?.key === key ? (sort.desc ? "▼" : "▲") : ""}
      </button>
    </th>
  );
  const anyFilter = !!(status || customer || sku || from || to);
  return (
    <Card title={data ? `Customer orders (${rows.length}${anyFilter ? ` of ${data.total}` : ""})` : "Customer orders"}>
      <div className="filters">
        <label>
          Status
          <select aria-label="Filter by status" value={status} onChange={(e) => (setStatus(e.target.value), setPage(0))}>
            <option value="">All statuses</option>
            {(data?.statuses ?? []).map((s: string) => <option key={s}>{s}</option>)}
          </select>
        </label>
        <label>
          Customer
          <select aria-label="Filter by customer" value={customer} onChange={(e) => (setCustomer(e.target.value), setPage(0))}>
            <option value="">All customers</option>
            {(data?.customers ?? []).map((c: Any) => <option key={c.customer_id} value={c.customer_id}>{c.customer_id} — {c.customer_name}</option>)}
          </select>
        </label>
        <label>
          Product
          <select aria-label="Filter by SKU" value={sku} onChange={(e) => (setSku(e.target.value), setPage(0))}>
            <option value="">All SKUs</option>
            {(data?.products ?? []).map((p: Any) => <option key={p.sku}>{p.sku}</option>)}
          </select>
        </label>
        <label>
          Delivery from
          <input aria-label="Delivery from" type="date" value={from} onChange={(e) => (setFrom(e.target.value), setPage(0))} />
        </label>
        <label>
          Delivery to
          <input aria-label="Delivery to" type="date" value={to} onChange={(e) => (setTo(e.target.value), setPage(0))} />
        </label>
      </div>
      {error ? (
        <p className="error" role="alert">Could not load customer orders: {error}</p>
      ) : !data && loading ? (
        <p className="muted">Loading customer orders…</p>
      ) : rows.length === 0 ? (
        <p className="muted">{anyFilter ? "No order matches the filter." : "No customer orders yet. Orders you create appear here with their production status."}</p>
      ) : (
        <div className="table-wrap">
          <table className="order-table">
            <thead>
              <tr>
                <th aria-label="Expand" />
                {head("order_number", "Order")}
                {head("customer_name", "Customer")}
                <th>Source</th>
                {head("requested_delivery_date", "Delivery")}
                <th>Lines</th>
                <th>Progress</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.slice(cur * PAGE, (cur + 1) * PAGE).map((o) => (
                <Fragment key={o.order_number}>
                  <tr>
                    <td>
                      <button className="ghost small" aria-expanded={open.has(o.order_number)} aria-label={`Lines of ${o.order_number}`} onClick={() => toggle(o.order_number)}>
                        {open.has(o.order_number) ? "▾" : "▸"}
                      </button>
                    </td>
                    <td>
                      <strong>{o.order_number}</strong>
                      {o.created_in_session && <> <Badge tone="info">New</Badge></>}
                      {o.at_risk && (
                        <span className="risk" title={`At risk of being late: completes ${o.estimated_completion}, ${o.days_late} day(s) after the requested delivery.`}> ⚠</span>
                      )}
                      {o.po_number && <div className="muted small">{o.po_number}</div>}
                    </td>
                    <td>{o.customer_name}</td>
                    <td>{o.channel_label}</td>
                    <td>{o.requested_delivery_date}</td>
                    <td>{o.lines.length} line{o.lines.length > 1 ? "s" : ""}</td>
                    <td>
                      <div className="progress" title={`Planned ${num(o.planned_qty)}`}>
                        <span className="bar"><span style={{ width: `${Math.min(100, o.progress_pct)}%` }} /></span>
                        <span className="small">{num(o.produced_qty)} / {num(o.ordered_qty)} ({Math.round(o.progress_pct)}%)</span>
                      </div>
                    </td>
                    <td><StatusPill status={o.status} /></td>
                    <td><button className="ghost small" onClick={() => onOpen(o.order_number)}>Detail</button></td>
                  </tr>
                  {open.has(o.order_number) && (
                    <tr className="sub">
                      <td colSpan={9}><OrderLines order={o} /></td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {pages > 1 && (
        <nav className="pager" aria-label="Pages">
          <span className="muted small">
            {num(cur * PAGE + 1)}–{num(Math.min((cur + 1) * PAGE, rows.length))} of {num(rows.length)}
          </span>
          <button className="small" onClick={() => setPage(cur - 1)} disabled={cur === 0}>‹ Previous</button>
          <span className="small">Page {cur + 1} of {pages}</span>
          <button className="small" onClick={() => setPage(cur + 1)} disabled={cur >= pages - 1}>Next ›</button>
        </nav>
      )}
      {data && <Source>{data.source}. At risk: {data.at_risk_rule}</Source>}
    </Card>
  );
}

export default function OrdersPage() {
  const { data, error, loading } = useData<Any>("/api/modules/orders/summary");
  const [params, setParams] = useSearchParams();
  const [meta, setMeta] = useState<OrderMeta | null>(null);
  // ?add=1 opens the wizard and ?order=<number> the detail, so a shared link lands on the form or the order.
  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  const b = data?.book;
  const applied = data?.customer_pos ?? [];
  return (
    <>
      <section className="hero">
        <div>
          <p className="hero-tag">Module 0 · start of the Order-to-Delivery chain</p>
          <h1>Customer Orders</h1>
          <p className="hero-sub">
            Firm orders and revised customer schedules. Status from Received to Produced is derived from the plan and from production, not typed. A PO change approved here
            flows into Forecast, Planning, Material and Purchasing.
          </p>
          <div className="row">
            <button className="primary big" onClick={() => setParam("add", "1")}>+ Create order</button>
          </div>
        </div>
      </section>
      <Status loading={loading} error={error} />
      {b && (
        <>
          <div className="kpis">
            <Kpi label="Open orders" value={num(b.open_count)} sub={`${num(b.open_qty)} bumpers`} />
            <Kpi label="At risk of being late" value={num(b.at_risk_count)} sub={`${num(b.due_this_week)} due this week`} tone={b.at_risk_count ? "bad" : "good"} />
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
        </>
      )}
      <OrderList onOpen={(n) => setParam("order", n)} onMeta={setMeta} />
      <PoIntake />
      {b && (
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
      )}
      <OrderWizard open={params.get("add") === "1"} meta={meta} onClose={() => setParam("add", null)} onView={(n) => setParams(new URLSearchParams({ order: n }), { replace: true })} />
      <OrderDetail orderNumber={params.get("order")} onClose={() => setParam("order", null)} />
    </>
  );
}
