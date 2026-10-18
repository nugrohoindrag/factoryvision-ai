/**
 * Create order, as a three-step wizard: ① Order details → ② Products → ③ Review → order created.
 *
 * The questions come in the order a planner thinks them: whose order and where it came from, what they ordered,
 * is it right. Each step checks its own fields; the review step asks the server, which re-checks everything and
 * adds the warnings it alone can see (a reference already on another order, a delivery the plant cannot make).
 */

import { useEffect, useMemo, useState } from "react";
import { Dialog, Field, Table, num } from "../components/ui";
import { ApiError, errorText, post } from "../lib/api";
import { formatIdr } from "../lib/format";
import { useApp } from "../lib/store";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

type Step = 1 | 2 | 3;
type FieldError = { field: string; message: string };
type Line = { key: string; sku: string; qty: number; requested_delivery_date: string };
export type OrderMeta = { as_of: string; customers: Any[]; products: Any[]; channels: Any[] };

const STEPS: { id: Step; label: string }[] = [
  { id: 1, label: "Order details" },
  { id: 2, label: "Products" },
  { id: 3, label: "Review" },
];

const newKey = () => `l-${Math.random().toString(36).slice(2, 9)}`;
const daysBetween = (a: string, b: string) => Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86_400_000);

function StepIndicator({ current, reachable, onJump }: { current: Step; reachable: Step; onJump: (s: Step) => void }) {
  return (
    <ol className="steps" aria-label="Steps">
      {STEPS.map((s) => {
        const state = s.id === current ? "active" : s.id < current ? "done" : "";
        const inner = (
          <>
            <span className="dot">{s.id < current ? "✓" : s.id}</span>
            {s.label}
          </>
        );
        return (
          <li key={s.id} className={state} aria-current={s.id === current ? "step" : undefined}>
            {s.id <= reachable && s.id !== current ? <button onClick={() => onJump(s.id)}>{inner}</button> : inner}
          </li>
        );
      })}
    </ol>
  );
}

function Input({ label, error, children }: { label: string; error?: string; children: React.ReactNode }) {
  return (
    <label>
      {label}
      {children}
      {error && <span className="field-error">{error}</span>}
    </label>
  );
}

export function OrderWizard({ open, meta, onClose, onView }: { open: boolean; meta: OrderMeta | null; onClose: () => void; onView: (orderNumber: string) => void }) {
  const { bump } = useApp();
  const asOf = meta?.as_of ?? "";
  const [step, setStep] = useState<Step>(1);
  const [reachable, setReachable] = useState<Step>(1);
  const [customerId, setCustomerId] = useState("");
  const [channel, setChannel] = useState("");
  const [reference, setReference] = useState("");
  const [orderDate, setOrderDate] = useState(asOf);
  const [delivery, setDelivery] = useState("");
  const [pic, setPic] = useState("");
  const [address, setAddress] = useState("");
  const [dock, setDock] = useState("");
  const [lines, setLines] = useState<Line[]>([]);
  const [panel, setPanel] = useState<Line | null>(null);
  const [panelErrors, setPanelErrors] = useState<FieldError[]>([]);
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [banner, setBanner] = useState<string | null>(null);
  const [review, setReview] = useState<{ warnings: string[]; preview: Any } | null>(null);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<Any>(null);

  useEffect(() => {
    if (!orderDate && asOf) setOrderDate(asOf); // the plant's date, not the browser's: the demo runs on 2027-02-01
  }, [asOf, orderDate]);

  const ch = meta?.channels.find((c: Any) => c.value === channel);
  const refLabel = ch?.reference_label ?? "PO number / reference";
  const refRequired = ch ? ch.reference_required : true;
  const customer = meta?.customers.find((c: Any) => c.customer_id === customerId);
  const productBySku = useMemo(() => Object.fromEntries((meta?.products ?? []).map((p: Any) => [p.sku, p])), [meta]);
  const errorFor = (f: string, list = errors) => list.find((e) => e.field === f)?.message;
  const total = lines.reduce((s, l) => s + l.qty, 0);

  const reset = () => {
    setStep(1);
    setReachable(1);
    setCustomerId("");
    setChannel("");
    setReference("");
    setOrderDate(asOf);
    setDelivery("");
    setPic("");
    setAddress("");
    setDock("");
    setLines([]);
    setPanel(null);
    setPanelErrors([]);
    setErrors([]);
    setBanner(null);
    setReview(null);
    setCreated(null);
  };
  const close = () => {
    if (busy) return;
    reset();
    onClose();
  };

  const pickCustomer = (id: string) => {
    setCustomerId(id);
    const c = meta?.customers.find((x: Any) => x.customer_id === id);
    if (!c) return;
    // The master's contact, address, dock and usual channel are the defaults; the planner can overwrite them.
    setPic(c.pic_name ?? "");
    setAddress(c.delivery_address ?? "");
    setDock(c.dock_number ?? "");
    if (!channel) setChannel(c.default_channel);
  };

  const body = () => ({
    customer_id: customerId,
    order_channel: channel,
    po_number: reference.trim() || null,
    order_date: orderDate || null,
    requested_delivery_date: delivery || null,
    customer_pic: pic || null,
    delivery_address: address || null,
    dock_number: dock || null,
    lines: lines.map((l) => ({ sku: l.sku, qty: l.qty, requested_delivery_date: l.requested_delivery_date || null })),
  });

  const validateDetails = () => {
    const e: FieldError[] = [];
    if (!customerId) e.push({ field: "customer_id", message: "Choose a customer." });
    if (!channel) e.push({ field: "order_channel", message: "Choose how this order was received." });
    if (refRequired && !reference.trim()) e.push({ field: "po_number", message: `${refLabel} is required.` });
    if (!orderDate) e.push({ field: "order_date", message: "Order date is required." });
    if (!delivery) e.push({ field: "requested_delivery_date", message: "Requested delivery date is required." });
    else if (orderDate && delivery < orderDate) e.push({ field: "requested_delivery_date", message: "Delivery cannot be before the order date." });
    else if (asOf && delivery < asOf) e.push({ field: "requested_delivery_date", message: `Delivery cannot be before today (${asOf}).` });
    setErrors(e);
    return e.length === 0;
  };

  const runReview = async () => {
    setBusy(true);
    setBanner(null);
    try {
      const r = await post<{ errors: FieldError[]; warnings: string[]; preview: Any }>("/api/orders/validate", body());
      if (r.errors.length) {
        setErrors(r.errors);
        setStep(r.errors.some((e) => e.field.startsWith("lines")) ? 2 : 1);
        setBanner("Some fields need fixing before the order can be created.");
        return;
      }
      setReview({ warnings: r.warnings, preview: r.preview });
      setStep(3);
      setReachable(3);
    } catch (e) {
      setBanner(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const next = () => {
    setBanner(null);
    if (step === 1 && validateDetails()) {
      setStep(2);
      setReachable((r) => (r < 2 ? 2 : r));
    } else if (step === 2) {
      if (!lines.length) {
        setErrors([{ field: "lines", message: "Add at least one product." }]);
        return;
      }
      setErrors([]);
      void runReview();
    }
  };

  const commitPanel = () => {
    if (!panel) return;
    const e: FieldError[] = [];
    if (!panel.sku) e.push({ field: "sku", message: "Choose a product first." });
    if (!(panel.qty > 0)) e.push({ field: "qty", message: "Quantity must be more than zero." });
    if (panel.requested_delivery_date && orderDate && panel.requested_delivery_date < orderDate)
      e.push({ field: "requested_delivery_date", message: "Delivery cannot be before the order date." });
    if (lines.some((l) => l.sku === panel.sku && l.key !== panel.key))
      e.push({ field: "sku", message: "This product is already on the order. Change the quantity of the existing line." });
    setPanelErrors(e);
    if (e.length) return;
    setLines((cur) => (cur.some((l) => l.key === panel.key) ? cur.map((l) => (l.key === panel.key ? panel : l)) : [...cur, panel]));
    setPanel(null);
    setErrors([]);
  };

  const create = async () => {
    setBusy(true);
    setBanner(null);
    try {
      const r = await post<{ order: Any }>("/api/orders", body());
      setCreated(r.order);
      bump();
    } catch (e) {
      const fields: FieldError[] = e instanceof ApiError ? ((e.body.detail as Any)?.fields ?? []) : [];
      setErrors(fields);
      setBanner(e instanceof ApiError ? e.body.message : errorText(e));
      if (fields.some((f) => f.field.startsWith("lines"))) setStep(2);
      else if (fields.length) setStep(1);
    } finally {
      setBusy(false);
    }
  };

  if (!meta) return null;

  return (
    <Dialog open={open} title="Create customer order" onClose={close} wide>
      {created ? (
        <>
          <div className="notice-box good" role="status">
            Order <strong>{created.order_number}</strong> created for {created.customer_name}: {created.lines.length} line(s), {num(created.ordered_qty)} pcs. It is recorded
            in the Action Center and already counts in Delivery's ATP check.
          </div>
          <div className="dialog-foot">
            <button onClick={reset}>Create another</button>
            <button onClick={close}>Close</button>
            <button
              className="primary"
              onClick={() => {
                const n = created.order_number;
                close();
                onView(n);
              }}
            >
              View order
            </button>
          </div>
        </>
      ) : (
        <>
          <StepIndicator current={step} reachable={reachable} onJump={(s) => (s === 3 ? void runReview() : setStep(s))} />
          {banner && <div className="notice-box bad" role="alert">{banner}</div>}

          {step === 1 && (
            <div className="form-grid">
              <Input label="Customer *" error={errorFor("customer_id")}>
                <select aria-label="Customer" value={customerId} onChange={(e) => pickCustomer(e.target.value)} className={errorFor("customer_id") ? "invalid" : ""}>
                  <option value="">Choose a customer</option>
                  {meta.customers.map((c: Any) => (
                    <option key={c.customer_id} value={c.customer_id}>{c.customer_id} — {c.customer_name}</option>
                  ))}
                </select>
              </Input>
              <Input label="Order source *" error={errorFor("order_channel")}>
                <select aria-label="Order source" value={channel} onChange={(e) => setChannel(e.target.value)} className={errorFor("order_channel") ? "invalid" : ""}>
                  <option value="">How did the order arrive?</option>
                  {meta.channels.map((c: Any) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
              </Input>
              <Input label={`${refLabel}${refRequired ? " *" : ""}`} error={errorFor("po_number")}>
                <input aria-label="Reference" value={reference} onChange={(e) => setReference(e.target.value)} className={errorFor("po_number") ? "invalid" : ""} />
              </Input>
              <Input label="Order date *" error={errorFor("order_date")}>
                <input aria-label="Order date" type="date" value={orderDate} onChange={(e) => setOrderDate(e.target.value)} />
              </Input>
              <Input label="Requested delivery date *" error={errorFor("requested_delivery_date")}>
                <input
                  aria-label="Requested delivery date"
                  type="date"
                  min={asOf}
                  value={delivery}
                  onChange={(e) => setDelivery(e.target.value)}
                  className={errorFor("requested_delivery_date") ? "invalid" : ""}
                />
              </Input>
              <Input label="Customer PIC">
                <input aria-label="Customer PIC" value={pic} onChange={(e) => setPic(e.target.value)} />
              </Input>
              <Input label="Delivery address">
                <input aria-label="Delivery address" value={address} onChange={(e) => setAddress(e.target.value)} />
              </Input>
              <Input label="Dock">
                <input aria-label="Dock" value={dock} onChange={(e) => setDock(e.target.value)} />
              </Input>
            </div>
          )}

          {step === 2 && (
            <>
              {lines.length === 0 && !panel && <p className="muted">No products yet. Add what the customer ordered.</p>}
              {lines.length > 0 && (
                <Table
                  cols={[
                    { key: "p", label: "Product" },
                    { key: "q", label: "Quantity", align: "right" },
                    { key: "d", label: "Delivery" },
                    { key: "a", label: "" },
                  ]}
                  rows={lines.map((l, i) => ({
                    p: (
                      <>
                        <strong>{l.sku}</strong> <span className="muted small">{productBySku[l.sku]?.name}</span>
                        {errorFor(`lines.${i}.sku`) && <div className="field-error">{errorFor(`lines.${i}.sku`)}</div>}
                      </>
                    ),
                    q: `${num(l.qty)} pcs`,
                    d: l.requested_delivery_date || <span className="muted">{delivery} (order)</span>,
                    a: (
                      <div className="row">
                        <button className="ghost small" onClick={() => (setPanelErrors([]), setPanel({ ...l }))}>Edit</button>
                        <button className="ghost small" onClick={() => setLines((cur) => cur.filter((x) => x.key !== l.key))}>Remove</button>
                      </div>
                    ),
                  }))}
                />
              )}
              {errorFor("lines") && <p className="field-error">{errorFor("lines")}</p>}
              {panel ? (
                <div className="card">
                  <div className="form-grid">
                    <Input label="Product *" error={errorFor("sku", panelErrors)}>
                      <select aria-label="Product" value={panel.sku} onChange={(e) => setPanel({ ...panel, sku: e.target.value })}>
                        <option value="">Choose a product</option>
                        {meta.products.map((p: Any) => <option key={p.sku} value={p.sku}>{p.sku} — {p.name}</option>)}
                      </select>
                    </Input>
                    <Input label="Quantity (pcs) *" error={errorFor("qty", panelErrors)}>
                      <input aria-label="Quantity" type="number" min={1} value={panel.qty || ""} onChange={(e) => setPanel({ ...panel, qty: Math.floor(Number(e.target.value)) })} />
                    </Input>
                    <Input label="Own delivery date (optional)" error={errorFor("requested_delivery_date", panelErrors)}>
                      <input aria-label="Line delivery date" type="date" min={orderDate} value={panel.requested_delivery_date} onChange={(e) => setPanel({ ...panel, requested_delivery_date: e.target.value })} />
                    </Input>
                  </div>
                  <div className="dialog-foot">
                    <button onClick={() => setPanel(null)}>Cancel</button>
                    <button className="primary" onClick={commitPanel}>{lines.some((l) => l.key === panel.key) ? "Save product" : "Add to order"}</button>
                  </div>
                </div>
              ) : (
                <div className="row">
                  <button onClick={() => (setPanelErrors([]), setPanel({ key: newKey(), sku: "", qty: 0, requested_delivery_date: "" }))}>+ Add product</button>
                  {lines.length > 0 && <span className="muted small">{lines.length} product(s) · {num(total)} pcs</span>}
                </div>
              )}
            </>
          )}

          {step === 3 && review && (
            <>
              <div className="fields">
                <Field label="Customer">{customer?.customer_name}</Field>
                <Field label="Order source">{ch?.label}</Field>
                <Field label={refLabel}>{reference || "–"}</Field>
                <Field label="Order date">{orderDate}</Field>
                <Field label="Requested delivery">{delivery}{delivery && orderDate ? ` (${daysBetween(orderDate, delivery)} days)` : ""}</Field>
                <Field label="Customer PIC">{pic || "–"}</Field>
                <Field label="Delivery address">{address || "–"}</Field>
                <Field label="Dock">{dock || "–"}</Field>
              </div>
              <Table
                cols={[
                  { key: "p", label: "Product" },
                  { key: "q", label: "Quantity", align: "right" },
                  { key: "d", label: "Delivery" },
                ]}
                rows={lines.map((l) => ({ p: <><strong>{l.sku}</strong> <span className="muted small">{productBySku[l.sku]?.name}</span></>, q: num(l.qty), d: l.requested_delivery_date || delivery }))}
              />
              <p className="small">Total: <strong>{num(total)} pcs</strong> in {lines.length} line(s).</p>
              {review.warnings.map((w) => <div key={w} className="notice-box warn">{w}</div>)}
              {review.preview && !review.preview.at_risk && <div className="notice-box good">At current capacity this order can be delivered on the requested date.</div>}
              {review.preview?.at_risk && <p className="muted small">Late-delivery penalty if nothing changes: {formatIdr(review.preview.penalty_idr)}.</p>}
            </>
          )}

          <div className="dialog-foot">
            {step > 1 && <button onClick={() => setStep((s) => (s - 1) as Step)} disabled={busy}>Back</button>}
            {step < 3 && <button className="primary" onClick={next} disabled={busy || !!panel}>{busy ? "Checking…" : "Next"}</button>}
            {step === 3 && <button className="primary" onClick={create} disabled={busy}>{busy ? "Creating…" : "Create order"}</button>}
          </div>
        </>
      )}
    </Dialog>
  );
}
