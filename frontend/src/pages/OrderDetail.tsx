/** Customer order detail: progress per line, the plans and work orders behind it, source documents, cancellation. */

import { useState } from "react";
import { Badge, Dialog, Field, Table, num } from "../components/ui";
import { ApiError, errorText, getBlob, post, postForm } from "../lib/api";
import { formatIdr } from "../lib/format";
import { useApp, useData } from "../lib/store";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

export const STATUS_TONE: Record<string, "info" | "warn" | "good" | "neutral" | "bad"> = {
  Received: "info",
  Planned: "info",
  "In Production": "warn",
  Produced: "good",
  "Ready to Ship": "good",
  Shipped: "good",
  Completed: "neutral",
  Cancelled: "bad",
};

const WO_TONE: Record<string, "info" | "warn" | "good" | "neutral"> = { "In Progress": "warn", Released: "info", Scheduled: "neutral", Completed: "good" };

export function StatusPill({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? "neutral"}>{status}</Badge>;
}

/** What was asked for, what planning committed, what has been produced, and when it ships; same table in the list and the dialog. */
export function OrderLines({ order }: { order: Any }) {
  return (
    <Table
      cols={[
        { key: "product", label: "Product" },
        { key: "color", label: "Colour" },
        { key: "ordered", label: "Ordered", align: "right" },
        { key: "planned", label: "Planned", align: "right" },
        { key: "produced", label: "Produced", align: "right" },
        { key: "ship", label: "Delivery" },
        { key: "status", label: "Status" },
      ]}
      rows={order.lines.map((l: Any) => ({
        product: (
          <>
            <strong>{l.sku}</strong> <span className="muted small">{l.product_name}</span>
          </>
        ),
        color: l.color ?? <span className="muted" title="No colour on this line: material planning splits it by usage share">–</span>,
        ordered: num(l.ordered_qty),
        planned: num(l.planned_qty),
        produced: num(l.produced_qty),
        ship: (
          <>
            {l.requested_delivery_date}
            {l.at_risk && <span className="risk small" title={`Completes ${l.estimated_completion}`}> · {l.days_late} d late</span>}
          </>
        ),
        status: <StatusPill status={l.status} />,
      }))}
    />
  );
}

async function openDocument(orderNumber: string, doc: Any) {
  const blob = await getBlob(`/api/orders/${encodeURIComponent(orderNumber)}/documents/${doc.doc_id}`);
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank", "noopener");
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

function Traceability({ plans }: { plans: Any[] }) {
  if (!plans.length) return <p className="muted small">This order is not in any production plan yet.</p>;
  return (
    <>
      {plans.map((p) => (
        <div key={p.plan_number}>
          <p className="small">
            <strong>{p.plan_number}</strong> <Badge tone={p.status === "In Execution" ? "warn" : p.status === "Completed" ? "good" : "info"}>{p.status}</Badge>{" "}
            <span className="muted">
              {p.lines.map((l: Any) => `${l.sku} on ${l.machine_id}${l.plan_qty != null ? ` (month plan ${num(l.plan_qty)})` : ""}`).join(" · ")}
            </span>
            {p.from_stock_qty > 0 && <span className="muted"> · {num(p.from_stock_qty)} pcs from finished-goods stock</span>}
          </p>
          <Table
            cols={[
              { key: "wo", label: "Work order" },
              { key: "date", label: "Date" },
              { key: "machine", label: "Machine" },
              { key: "qty", label: "Qty for this order", align: "right" },
              { key: "status", label: "Status" },
            ]}
            rows={p.work_orders.map((w: Any) => ({
              wo: <strong>{w.wo_id}</strong>,
              date: w.date,
              machine: w.machine_id,
              qty: num(w.qty),
              status: <Badge tone={WO_TONE[w.status] ?? "neutral"}>{w.status}</Badge>,
            }))}
            empty="Covered from finished-goods stock; no work order needed."
          />
        </div>
      ))}
    </>
  );
}

function Documents({ order }: { order: Any }) {
  const { bump } = useApp();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const upload = async (file: File) => {
    setBusy(true);
    setErr(null);
    const f = new FormData();
    f.append("file", file);
    try {
      await postForm(`/api/orders/${encodeURIComponent(order.order_number)}/documents`, f);
      bump();
    } catch (e) {
      setErr(e instanceof ApiError ? e.body.message : errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div>
      <h3>Source documents</h3>
      {order.documents.length === 0 ? (
        <p className="muted small">No documents yet. Attach the PO, kanban card or email so the origin of the demand can be checked later.</p>
      ) : (
        <ul className="doc-list small">
          {order.documents.map((d: Any) => (
            <li key={d.doc_id}>
              <a href="#" onClick={(e) => (e.preventDefault(), openDocument(order.order_number, d).catch((x) => setErr(errorText(x))))}>{d.file_name}</a>{" "}
              <span className="muted">({Math.max(1, Math.round(d.size_bytes / 1024))} KB, {d.uploaded_by}, {d.uploaded_at.slice(0, 16).replace("T", " ")})</span>
            </li>
          ))}
        </ul>
      )}
      <label className="upload">
        ⬆ {busy ? "Uploading…" : "Upload document"}
        <input
          type="file"
          aria-label="Upload a source document"
          accept=".pdf,.png,.jpg,.jpeg,.txt,.csv,.eml,.xlsx"
          disabled={busy}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void upload(file);
            e.target.value = "";
          }}
        />
      </label>
      {err && <p className="error small" role="alert">{err}</p>}
    </div>
  );
}

function CancelDialog({ order, open, onClose }: { order: Any; open: boolean; onClose: () => void }) {
  const { bump } = useApp();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const confirm = async () => {
    setBusy(true);
    setErr(null);
    try {
      await post(`/api/orders/${encodeURIComponent(order.order_number)}/cancel`, { reason });
      setReason("");
      onClose();
      bump();
    } catch (e) {
      setErr(e instanceof ApiError ? e.body.message : errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} title="Cancel customer order" onClose={onClose}>
      <p className="muted small">
        Cancelling is refused once a work order for this order is in production. The reason is required and is recorded in the Action Center.
      </p>
      <div className="form-grid">
        <label>
          Cancellation reason
          <input value={reason} onChange={(e) => setReason(e.target.value)} className={err ? "invalid" : ""} autoFocus />
          {err && <span className="field-error" role="alert">{err}</span>}
        </label>
      </div>
      <div className="dialog-foot">
        <button onClick={onClose}>Back</button>
        <button className="primary" disabled={reason.trim().length < 3 || busy} onClick={confirm}>Confirm cancellation</button>
      </div>
    </Dialog>
  );
}

export function OrderDetail({ orderNumber, onClose }: { orderNumber: string | null; onClose: () => void }) {
  const { data: o, error, loading } = useData<Any>(orderNumber ? `/api/orders/${encodeURIComponent(orderNumber)}` : null);
  const [cancelling, setCancelling] = useState(false);
  const shown = o && o.order_number === orderNumber ? o : null;
  return (
    <Dialog open={!!orderNumber} title={shown ? `Order ${shown.order_number}` : "Order detail"} onClose={onClose} wide>
      {loading && !shown && <p className="muted">Loading the order…</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {shown && (
        <>
          <div className="fields">
            <Field label="Customer">{shown.customer_name}</Field>
            <Field label="Order source">{shown.channel_label}</Field>
            <Field label="Customer PO / reference">{shown.po_number ?? "–"}</Field>
            <Field label="Order date">{shown.order_date}</Field>
            <Field label="Requested delivery">{shown.requested_delivery_date}</Field>
            <Field label="Dock">{shown.dock_number ?? "–"}</Field>
            <Field label="Customer PIC">{shown.customer_pic ?? "–"}</Field>
            <Field label="Delivery address">{shown.delivery_address ?? "–"}</Field>
            <Field label="Status"><StatusPill status={shown.status} /></Field>
            {shown.created_by && <Field label="Entered by">{shown.created_by}</Field>}
          </div>
          {shown.status_reason && <div className="notice-box warn">Reason: {shown.status_reason}</div>}
          {shown.at_risk && (
            <div className="notice-box bad" role="status">
              At risk of being late: at current capacity it completes on {shown.estimated_completion}, {shown.days_late} day(s) after the requested delivery
              (penalty {formatIdr(shown.penalty_idr)}). Revise the ship date or add capacity in Delivery and Planning.
            </div>
          )}
          <div>
            <h3>Order lines</h3>
            <OrderLines order={shown} />
          </div>
          <div>
            <h3>Production plans &amp; work orders serving this order</h3>
            <Traceability plans={shown.traceability} />
          </div>
          <Documents order={shown} />
          <div className="dialog-foot">
            {!shown.cancellable && shown.status !== "Cancelled" && <span className="muted small">{shown.cancel_block_reason}</span>}
            <button disabled={!shown.cancellable} onClick={() => setCancelling(true)}>Cancel order</button>
          </div>
          <CancelDialog order={shown} open={cancelling} onClose={() => setCancelling(false)} />
        </>
      )}
    </Dialog>
  );
}
