/**
 * Module 5, Purchasing: from a shortage to material on the shelf.
 *
 * Five tabs. Overview keeps the vendor scorecard and the urgent resin PO, plus open/overdue KPIs. Requisitions (PR)
 * go Draft → Submitted → Approved → Converted; Purchase Orders (PO) go Draft → Issued → (Partially) Received → Closed.
 * Issuing a PO raises incoming for Material Readiness and MRP; a goods receipt raises on hand. Vendors shows the
 * scorecard and recommends a vendor for any material. Every number comes from the API; the page only arranges it.
 */

import { useEffect, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { AskAgent } from "../components/agents";
import { Badge, Card, Dialog, Field, Kpi, Source, Status, Table, num, pct } from "../components/ui";
import { errorText, get, getBlob, post } from "../lib/api";
import { formatIdr, formatIdrCompact } from "../lib/format";
import { useApp, useData } from "../lib/store";
import type {
  DocHistory,
  GoodsReceipt,
  PurchaseOrder,
  PurchaseRequisition,
  PurchasingKpis,
  Vendor,
  VendorDetail,
  VendorMaterial,
  VendorRecommendation,
} from "../lib/types";
import { qty } from "./Material";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

const TABS = [
  ["overview", "Overview"],
  ["requisitions", "Requisitions"],
  ["orders", "Purchase Orders"],
  ["receipts", "Receipts"],
  ["vendors", "Vendors"],
] as const;
type Tab = (typeof TABS)[number][0];

const PR_STATUSES = ["Draft", "Submitted", "Approved", "Rejected", "Cancelled", "Converted"];
const PO_STATUSES = ["Draft", "Issued", "Partially Received", "Received", "Closed", "Cancelled"];
const PR_TONE: Record<string, "neutral" | "info" | "good" | "bad" | "warn"> = { Draft: "neutral", Submitted: "info", Approved: "good", Rejected: "bad", Cancelled: "neutral", Converted: "good" };
const PO_TONE: Record<string, "neutral" | "info" | "good" | "bad" | "warn"> = { Draft: "neutral", Issued: "info", "Partially Received": "warn", Received: "good", Closed: "neutral", Cancelled: "neutral" };
const TOLERANCE = 0.05;

const PUR = "/api/purchasing";
const enc = encodeURIComponent;
const dt = (iso: string | null | undefined) => (iso ? iso.slice(0, 16).replace("T", " ") : "–");
const today = () => new Date().toISOString().slice(0, 10);
/** R05: a price of 0 (or none) means the vendor quotes on request; it is never shown as Rp0. */
const price = (v: number | null | undefined) => (v ? formatIdr(v) : <span className="muted">price on quotation</span>);

function listOf<T>(d: Any, ...keys: string[]): T[] {
  if (Array.isArray(d)) return d as T[];
  for (const k of keys) if (Array.isArray(d?.[k])) return d[k] as T[];
  return [];
}

/** The first of several keys that holds a value: the summary KPIs are read defensively. */
function pick(o: Any, ...keys: string[]): number | null {
  for (const k of keys) if (o?.[k] != null) return o[k] as number;
  return null;
}

const materialId = (m: VendorMaterial) => (typeof m === "string" ? m : m.material_id);
const leadTime = (v: Partial<Vendor> | null | undefined) => v?.quoted_lead_time_days ?? v?.lead_time_days ?? null;

function PrBadge({ status }: { status: string | null | undefined }) {
  return <Badge tone={PR_TONE[status ?? ""] ?? "neutral"}>{status ?? "–"}</Badge>;
}

function PoBadge({ status }: { status: string | null | undefined }) {
  return <Badge tone={PO_TONE[status ?? ""] ?? "neutral"}>{status ?? "–"}</Badge>;
}

function ReceivedBar({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="muted">–</span>;
  return (
    <div className="progress" aria-label={`Received ${pct(value, 0)}`}>
      <span className="bar"><span style={{ width: `${Math.max(0, Math.min(100, value))}%`, background: value >= 100 ? "var(--good)" : "var(--accent)" }} /></span>
      <span className="small">{pct(value, 0)}</span>
    </div>
  );
}

function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children && <div className="muted">{children}</div>}
    </div>
  );
}

/** One mutation at a time: busy label, the server's error text, then a refetch of every view. */
function useMutation() {
  const { bump } = useApp();
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const run = async <T,>(label: string, fn: () => Promise<T>): Promise<T | null> => {
    setBusy(label);
    setErr(null);
    try {
      const r = await fn();
      bump();
      return r;
    } catch (e) {
      setErr(errorText(e));
      return null;
    } finally {
      setBusy(null);
    }
  };
  return { busy, err, setErr, run };
}

function History({ rows }: { rows: DocHistory[] | null | undefined }) {
  return (
    <Table
      empty="No history yet."
      cols={[{ key: "at", label: "When" }, { key: "by", label: "Who" }, { key: "a", label: "Action" }, { key: "s", label: "Status" }, { key: "r", label: "Reason" }]}
      rows={(rows ?? []).map((h) => ({ at: dt(h.at), by: h.by ?? "–", a: h.action, s: h.from || h.to ? `${h.from ?? "–"} → ${h.to ?? "–"}` : "–", r: h.reason ?? "" }))}
    />
  );
}

/* ---------------- Overview ---------------- */

function OverviewTab({ onPo, onPr }: { onPo: (id: string) => void; onPr: (id: string) => void }) {
  const { data, error, loading } = useData<Any>("/api/modules/purchasing/summary");
  const k: PurchasingKpis | null = data?.kpis ?? null;
  const overdue = listOf<PurchaseOrder>(data?.overdue, "orders");
  const pending = listOf<PurchaseRequisition>(data?.pending_requisitions, "requisitions");
  const resin = data?.resin_po;
  const placed = listOf<Any>(data?.placed);
  return (
    <>
      <Status loading={loading} error={error} />
      {k && (
        <div className="kpis">
          <Kpi label="Open POs" value={num(pick(k, "open_pos", "open_po_count", "open"))} />
          <Kpi label="Overdue POs" value={num(pick(k, "overdue_pos", "overdue_po_count", "overdue"))} tone={pick(k, "overdue_pos", "overdue_po_count", "overdue") ? "bad" : "good"} />
          <Kpi label="Value open" value={pick(k, "open_value_idr", "value_open_idr") == null ? "–" : formatIdrCompact(pick(k, "open_value_idr", "value_open_idr") ?? 0)} />
          <Kpi label="PRs waiting for approval" value={num(pick(k, "pending_requisitions", "pending_prs", "prs_pending_approval"))} tone={pick(k, "pending_requisitions", "pending_prs", "prs_pending_approval") ? "warn" : undefined} />
        </div>
      )}
      {data?.overdue && (
        <Card title={`Overdue purchase orders (${overdue.length})`}>
          <Table
            empty="No PO is overdue."
            cols={[{ key: "po", label: "PO" }, { key: "v", label: "Vendor" }, { key: "e", label: "Expected" }, { key: "l", label: "Days late", align: "right" }, { key: "r", label: "Received" }, { key: "x", label: "" }]}
            rows={overdue.map((p) => ({
              po: <strong>{p.po_id}</strong>,
              v: p.vendor_name ?? p.vendor_id,
              e: p.expected_date ?? "–",
              l: <span className="neg">{num(p.days_late)}</span>,
              r: <ReceivedBar value={p.received_pct} />,
              x: <button className="ghost small" aria-label={`Open ${p.po_id}`} onClick={() => onPo(p.po_id)}>Open</button>,
            }))}
          />
        </Card>
      )}
      {data?.pending_requisitions && (
        <Card title={`Requisitions waiting for approval (${pending.length})`}>
          <Table
            empty="No requisition is waiting."
            cols={[{ key: "pr", label: "PR" }, { key: "s", label: "Status" }, { key: "n", label: "Lines", align: "right" }, { key: "src", label: "Source" }, { key: "x", label: "" }]}
            rows={pending.map((r) => ({
              pr: <strong>{r.pr_id}</strong>,
              s: <PrBadge status={r.status} />,
              n: num(r.lines?.length ?? 0),
              src: r.source ?? "–",
              x: <button className="ghost small" aria-label={`Open ${r.pr_id}`} onClick={() => onPr(r.pr_id)}>Open</button>,
            }))}
          />
        </Card>
      )}
      {data?.scorecard && (
        <Card title="Vendor scorecard (90 days)">
          <Table
            cols={[{ key: "v", label: "Vendor" }, { key: "c", label: "Category" }, { key: "n", label: "POs", align: "right" }, { key: "ot", label: "On time", align: "right" }, { key: "d", label: "Avg delay", align: "right" }, { key: "lt", label: "Lead time (quoted / actual)", align: "right" }]}
            rows={listOf<Any>(data.scorecard.vendors).map((v) => ({ v: v.vendor_name, c: v.category, n: v.po_count, ot: <OnTime value={v.on_time_pct} />, d: `${v.avg_delay_days ?? "–"} d`, lt: `${v.quoted_lead_time_days ?? "–"} / ${v.effective_lead_time_days ?? "–"} d` }))}
          />
          {data.scorecard.source && <Source>{data.scorecard.source}</Source>}
        </Card>
      )}
      {resin && (
        <Card title={`Urgent resin PO: need by ${resin.need_by ?? "–"}`}>
          <Table
            cols={[{ key: "v", label: "Vendor" }, { key: "a", label: "Expected arrival" }, { key: "ok", label: "In time?" }, { key: "p", label: "PO value", align: "right" }]}
            rows={listOf<Any>(resin.options).map((o) => ({ v: o.vendor_name, a: `${o.expected_arrival} (+${o.expected_delay_days} d delay)`, ok: o.arrives_before_need ? <Badge tone="good">yes</Badge> : <Badge tone="bad">no</Badge>, p: price(o.po_value_idr) }))}
          />
          <p>Recommended: <strong>{resin.recommended_vendor ?? "–"}</strong> (premium {formatIdr(resin.premium_vs_cheapest_idr ?? 0)}).</p>
          {placed.length > 0 && <p><Badge tone="good">approved</Badge> {placed.map((p) => `${num(p.qty)} kg arriving ${p.expected_date}`).join("; ")}</p>}
          <p className="small">Inbox: <a href="/api/documents/vendor-email-andalas.txt" target="_blank" rel="noreferrer">email from PT Andalas Polimindo</a></p>
        </Card>
      )}
    </>
  );
}

function OnTime({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="muted">–</span>;
  return <Badge tone={value < 50 ? "bad" : value < 85 ? "warn" : "good"}>{pct(value)}</Badge>;
}

/* ---------------- Requisitions ---------------- */

type DraftLine = { material_id: string; qty: string; need_by: string };
const blankLine = (): DraftLine => ({ material_id: "", qty: "", need_by: "" });

/** PU-R04 live: the vendor the API would pick for this material, quantity and need date. */
function SuggestedVendor({ line }: { line: DraftLine }) {
  const [rec, setRec] = useState<VendorRecommendation | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const q = Number(line.qty);
  const ready = !!line.material_id && q > 0 && !!line.need_by;
  useEffect(() => {
    if (!ready) {
      setRec(null);
      setErr(null);
      return;
    }
    let alive = true;
    const qs = new URLSearchParams({ material_id: line.material_id, qty: String(q), need_by: line.need_by }).toString();
    get<VendorRecommendation>(`${PUR}/recommend?${qs}`)
      .then((r) => alive && (setRec(r), setErr(null)))
      .catch((e) => alive && (setRec(null), setErr(errorText(e))));
    return () => {
      alive = false;
    };
  }, [ready, line.material_id, q, line.need_by]);
  if (!ready) return <span className="muted small">Pick material, qty and need date to see a vendor.</span>;
  if (err) return <span className="field-error">{err}</span>;
  if (!rec) return <span className="muted small">Finding a vendor…</span>;
  const best = (rec.options ?? []).find((o) => o.vendor_id === rec.recommended_vendor_id);
  return (
    <span className="small" data-testid="suggested-vendor">
      Suggested: <strong>{rec.recommended_vendor ?? "–"}</strong>
      {best && <> · arrives {best.expected_arrival ?? "–"} {best.arrives_before_need === false ? <Badge tone="bad">late</Badge> : best.arrives_before_need ? <Badge tone="good">in time</Badge> : null} · {price(best.po_value_idr)}</>}
    </span>
  );
}

function NewRequisitionDialog({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const materials = listOf<Any>(useData<Any>(open ? "/api/master/materials" : null).data, "materials");
  const [lines, setLines] = useState<DraftLine[]>([blankLine()]);
  const [note, setNote] = useState("");
  const m = useMutation();
  const bad = lines.map((l) => (!l.material_id ? "Pick a material." : !(Number(l.qty) > 0) ? "Qty must be above 0." : !l.need_by ? "Pick a need date." : null));
  const touched = lines.map((l) => !!(l.material_id || l.qty || l.need_by));
  const setLine = (i: number, patch: Partial<DraftLine>) => setLines((ls) => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const close = () => {
    if (m.busy) return;
    setLines([blankLine()]);
    setNote("");
    m.setErr(null);
    onClose();
  };
  const create = async () => {
    const body = { lines: lines.map((l) => ({ material_id: l.material_id, qty: Number(l.qty), need_by: l.need_by })), ...(note ? { note } : {}) };
    const pr = await m.run("create", () => post<PurchaseRequisition>(`${PUR}/requisitions`, body));
    if (pr?.pr_id) {
      setLines([blankLine()]);
      setNote("");
      onClose();
      onCreated(pr.pr_id);
    }
  };
  return (
    <Dialog open={open} title="New purchase requisition" onClose={close} wide>
      <p className="muted small">The PR starts as Draft. Submit it for approval; once approved it converts to one PO per vendor.</p>
      {lines.map((l, i) => (
        <div key={i} className="pur-line">
          <div className="form-grid">
            <label>
              Material
              <select aria-label={`Material of line ${i + 1}`} value={l.material_id} onChange={(e) => setLine(i, { material_id: e.target.value })}>
                <option value="">Choose a material</option>
                {materials.map((x) => <option key={x.material_id} value={x.material_id}>{x.material_id}{x.material_name ? ` · ${x.material_name}` : ""}</option>)}
              </select>
            </label>
            <label>
              Qty
              <input type="number" min="0" aria-label={`Qty of line ${i + 1}`} value={l.qty} onChange={(e) => setLine(i, { qty: e.target.value })} className={touched[i] && l.qty !== "" && !(Number(l.qty) > 0) ? "invalid" : ""} />
            </label>
            <label>
              Need by
              <input type="date" aria-label={`Need by of line ${i + 1}`} value={l.need_by} onChange={(e) => setLine(i, { need_by: e.target.value })} />
            </label>
          </div>
          <div className="row">
            <SuggestedVendor line={l} />
            {touched[i] && bad[i] && <span className="field-error">{bad[i]}</span>}
            {lines.length > 1 && <button className="ghost small" aria-label={`Remove line ${i + 1}`} onClick={() => setLines((ls) => ls.filter((_, j) => j !== i))}>Remove</button>}
          </div>
        </div>
      ))}
      <div className="row">
        <button className="small" onClick={() => setLines((ls) => [...ls, blankLine()])}>+ Add line</button>
      </div>
      <div className="form-grid">
        <label>
          Note
          <input aria-label="Requisition note" value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
      </div>
      {m.err && <p className="error" role="alert">{m.err}</p>}
      <div className="dialog-foot">
        <button className="ghost" onClick={close} disabled={!!m.busy}>Cancel</button>
        <button className="primary" onClick={create} disabled={!!m.busy || bad.some(Boolean)}>{m.busy ? "Creating…" : "Create requisition"}</button>
      </div>
    </Dialog>
  );
}

function RequisitionList({ onPr }: { onPr: (id: string) => void }) {
  const [status, setStatus] = useState("");
  const [creating, setCreating] = useState(false);
  const { data, error, loading } = useData<Any>(`${PUR}/requisitions${status ? `?status=${enc(status)}` : ""}`);
  const rows = listOf<PurchaseRequisition>(data, "requisitions");
  return (
    <>
      <Card title="Purchase requisitions" actions={<button className="primary" onClick={() => setCreating(true)}>+ New requisition</button>}>
        <div className="filters">
          <label>
            Status
            <select aria-label="Filter requisitions by status" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All statuses</option>
              {PR_STATUSES.map((s) => <option key={s}>{s}</option>)}
            </select>
          </label>
        </div>
        {error ? (
          <p className="error" role="alert">{error}</p>
        ) : !data && loading ? (
          <p className="muted">Loading requisitions…</p>
        ) : rows.length === 0 ? (
          <Empty title="No requisition">Create one here, or from an MRP run or a readiness check on the Material page.</Empty>
        ) : (
          <Table
            cols={[{ key: "pr", label: "PR" }, { key: "s", label: "Status" }, { key: "c", label: "Created" }, { key: "src", label: "Source" }, { key: "n", label: "Lines", align: "right" }, { key: "nb", label: "Earliest need" }, { key: "po", label: "POs" }, { key: "x", label: "" }]}
            rows={rows.map((r) => {
              const lines = r.lines ?? [];
              const needs = lines.map((l) => l.need_by).filter(Boolean).sort();
              return {
                pr: <><strong>{r.pr_id}</strong> {lines.some((l) => l.duplicate_of) && <Badge tone="warn">duplicate</Badge>}</>,
                s: <PrBadge status={r.status} />,
                c: `${dt(r.created_at)} · ${r.created_by ?? "–"}`,
                src: r.source ?? "–",
                n: num(lines.length),
                nb: needs[0] ?? "–",
                po: (r.po_ids ?? []).join(", ") || "–",
                x: <button className="ghost small" aria-label={`Details of ${r.pr_id}`} onClick={() => onPr(r.pr_id)}>Details</button>,
              };
            })}
          />
        )}
      </Card>
      <NewRequisitionDialog open={creating} onClose={() => setCreating(false)} onCreated={onPr} />
    </>
  );
}

function ConvertDialog({ pr, open, onClose, onPo }: { pr: PurchaseRequisition; open: boolean; onClose: () => void; onPo: (id: string) => void }) {
  const vendors = listOf<Vendor>(useData<Any>(open ? `${PUR}/vendors` : null).data, "vendors");
  const [over, setOver] = useState<Record<string, string>>({});
  const [done, setDone] = useState<string[] | null>(null);
  const m = useMutation();
  const materials = [...new Map((pr.lines ?? []).map((l) => [l.material_id, l])).values()];
  const close = () => {
    if (m.busy) return;
    setOver({});
    setDone(null);
    m.setErr(null);
    onClose();
  };
  const convert = async () => {
    const changed = Object.fromEntries(Object.entries(over).filter(([mid, v]) => v && v !== materials.find((l) => l.material_id === mid)?.suggested_vendor_id));
    const r = await m.run("convert", () => post<{ po_ids: string[] }>(`${PUR}/requisitions/${enc(pr.pr_id)}/convert`, Object.keys(changed).length ? { vendor_overrides: changed } : {}));
    if (r) setDone(r.po_ids ?? []);
  };
  return (
    <Dialog open={open} title={`Convert ${pr.pr_id} to PO`} onClose={close} wide>
      {done ? (
        <>
          <div className="notice-box good" role="status">Created {done.length} purchase order(s): {done.join(", ") || "–"}</div>
          <div className="row">{done.map((id) => <button key={id} className="small" onClick={() => (close(), onPo(id))}>Open {id}</button>)}</div>
        </>
      ) : (
        <>
          <p className="muted small">Lines are grouped by vendor: one PO per vendor. Change a vendor to override the suggestion.</p>
          <Table
            cols={[{ key: "m", label: "Material" }, { key: "q", label: "Qty", align: "right" }, { key: "v", label: "Vendor" }]}
            rows={materials.map((l) => {
              const supplying = vendors.filter((v) => (v.materials ?? []).some((x) => materialId(x) === l.material_id));
              return {
                m: <><strong>{l.material_id}</strong> <span className="muted small">{l.material_name}</span></>,
                q: `${qty(l.qty)} ${l.unit ?? ""}`,
                v: (
                  <select aria-label={`Vendor for ${l.material_id}`} value={over[l.material_id] ?? l.suggested_vendor_id ?? ""} onChange={(e) => setOver((o) => ({ ...o, [l.material_id]: e.target.value }))}>
                    {!l.suggested_vendor_id && <option value="">Choose a vendor</option>}
                    {l.suggested_vendor_id && !supplying.some((v) => v.vendor_id === l.suggested_vendor_id) && <option value={l.suggested_vendor_id}>{l.suggested_vendor_name ?? l.suggested_vendor_id} (suggested)</option>}
                    {supplying.map((v) => <option key={v.vendor_id} value={v.vendor_id}>{v.vendor_name}{v.vendor_id === l.suggested_vendor_id ? " (suggested)" : ""}</option>)}
                  </select>
                ),
              };
            })}
          />
          {m.err && <p className="error" role="alert">{m.err}</p>}
          <div className="dialog-foot">
            <button className="ghost" onClick={close} disabled={!!m.busy}>Cancel</button>
            <button className="primary" onClick={convert} disabled={!!m.busy}>{m.busy ? "Converting…" : "Create purchase orders"}</button>
          </div>
        </>
      )}
    </Dialog>
  );
}

function RequisitionDetail({ prId, onBack, onPo }: { prId: string; onBack: () => void; onPo: (id: string) => void }) {
  const { data: pr, error, loading } = useData<PurchaseRequisition>(`${PUR}/requisitions/${enc(prId)}`);
  const m = useMutation();
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [converting, setConverting] = useState(false);
  const act = (action: string, body?: unknown) => m.run(action, () => post<PurchaseRequisition>(`${PUR}/requisitions/${enc(prId)}/${action}`, body ?? {}));
  const reject = async () => {
    if (await act("reject", { reason: reason.trim() })) {
      setRejecting(false);
      setReason("");
    }
  };
  const back = <button className="ghost small" onClick={onBack}>← All requisitions</button>;
  if (error) return <Card title={prId} actions={back}><p className="error" role="alert">{error}</p></Card>;
  if (!pr || pr.pr_id !== prId) return <Card title={prId} actions={back}><p className="muted">{loading ? "Loading the requisition…" : "Not found."}</p></Card>;
  const s = pr.status;
  const btn = (label: string, action: string, primary = false) => (
    <button className={primary ? "primary" : ""} disabled={!!m.busy} onClick={() => act(action)}>{m.busy === action ? `${label}…` : label}</button>
  );
  return (
    <>
      <Card title={<>{pr.pr_id} <PrBadge status={s} /></>} actions={back}>
        <div className="fields">
          <Field label="Created">{dt(pr.created_at)} · {pr.created_by ?? "–"}</Field>
          <Field label="Source">{pr.source ?? "manual"}</Field>
          <Field label="Note">{pr.note || "–"}</Field>
          <Field label="Purchase orders">
            {(pr.po_ids ?? []).length ? pr.po_ids.map((id) => <button key={id} className="ghost small" onClick={() => onPo(id)}>{id}</button>) : "–"}
          </Field>
        </div>
        <div className="row pur-actions">
          {s === "Draft" && btn("Submit", "submit", true)}
          {s === "Submitted" && btn("Approve", "approve", true)}
          {s === "Submitted" && <button disabled={!!m.busy} onClick={() => setRejecting(true)}>Reject</button>}
          {(s === "Draft" || s === "Submitted") && btn("Cancel", "cancel")}
          {s === "Approved" && <button className="primary" disabled={!!m.busy} onClick={() => setConverting(true)}>Convert to PO</button>}
        </div>
        {m.err && !rejecting && <p className="error" role="alert">{m.err}</p>}
      </Card>
      <Card title="Lines">
        <Table
          cols={[{ key: "m", label: "Material" }, { key: "q", label: "Qty", align: "right" }, { key: "n", label: "Need by" }, { key: "v", label: "Suggested vendor" }, { key: "src", label: "Source" }, { key: "d", label: "" }]}
          rows={(pr.lines ?? []).map((l) => ({
            m: <><strong>{l.material_id}</strong> <span className="muted small">{l.material_name}</span></>,
            q: `${qty(l.qty)} ${l.unit ?? ""}`,
            n: l.need_by ?? "–",
            v: l.suggested_vendor_name ?? l.suggested_vendor_id ?? "–",
            src: l.source ?? "–",
            d: l.duplicate_of ? <Badge tone="warn">Duplicate of {l.duplicate_of}</Badge> : null,
          }))}
        />
        {(pr.lines ?? []).some((l) => l.duplicate_of) && <p className="muted small">A duplicate line asks for a material and need date already on an open PR or PO from the same source. Check before approving.</p>}
      </Card>
      <Card title="History">
        <History rows={pr.history} />
      </Card>
      <Dialog open={rejecting} title={`Reject ${pr.pr_id}`} onClose={() => !m.busy && (setRejecting(false), m.setErr(null))}>
        <div className="form-grid">
          <label>
            Reason
            <input aria-label="Reject reason" value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
        </div>
        {m.err && <p className="error" role="alert">{m.err}</p>}
        <div className="dialog-foot">
          <button className="ghost" onClick={() => setRejecting(false)} disabled={!!m.busy}>Back</button>
          <button className="primary" onClick={reject} disabled={!!m.busy || !reason.trim()}>{m.busy ? "Rejecting…" : "Reject requisition"}</button>
        </div>
      </Dialog>
      <ConvertDialog pr={pr} open={converting} onClose={() => setConverting(false)} onPo={onPo} />
    </>
  );
}

/* ---------------- Purchase orders ---------------- */

function OrderList({ onPo }: { onPo: (id: string) => void }) {
  const [status, setStatus] = useState("");
  const [vendor, setVendor] = useState("");
  const [overdue, setOverdue] = useState(false);
  const qs = new URLSearchParams(Object.entries({ status, vendor_id: vendor, overdue: overdue ? "true" : "" }).filter(([, v]) => v)).toString();
  const { data, error, loading } = useData<Any>(`${PUR}/orders${qs ? `?${qs}` : ""}`);
  const vendors = listOf<Vendor>(useData<Any>(`${PUR}/vendors`).data, "vendors");
  const rows = listOf<PurchaseOrder>(data, "orders").filter((p) => !overdue || p.overdue);
  return (
    <Card title="Purchase orders">
      <div className="filters">
        <label>
          Status
          <select aria-label="Filter orders by status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            {PO_STATUSES.map((s) => <option key={s}>{s}</option>)}
          </select>
        </label>
        <label>
          Vendor
          <select aria-label="Filter orders by vendor" value={vendor} onChange={(e) => setVendor(e.target.value)}>
            <option value="">All vendors</option>
            {vendors.map((v) => <option key={v.vendor_id} value={v.vendor_id}>{v.vendor_name}</option>)}
          </select>
        </label>
        <label className="check">
          <input type="checkbox" aria-label="Only overdue" checked={overdue} onChange={(e) => setOverdue(e.target.checked)} /> Only overdue
        </label>
      </div>
      {error ? (
        <p className="error" role="alert">{error}</p>
      ) : !data && loading ? (
        <p className="muted">Loading purchase orders…</p>
      ) : (
        <Table
          empty="No purchase order matches the filter."
          cols={[{ key: "po", label: "PO" }, { key: "v", label: "Vendor" }, { key: "s", label: "Status" }, { key: "o", label: "Ordered" }, { key: "e", label: "Expected" }, { key: "val", label: "Value", align: "right" }, { key: "r", label: "Received" }, { key: "x", label: "" }]}
          rows={rows.map((p) => ({
            po: <><strong>{p.po_id}</strong> {p.origin === "seed" && <Badge tone="info">seed</Badge>}</>,
            v: p.vendor_name ?? p.vendor_id,
            s: <><PoBadge status={p.status} /> {p.overdue && <Badge tone="bad">overdue {num(p.days_late)} d</Badge>}</>,
            o: p.order_date ?? "–",
            e: p.expected_date ?? "–",
            val: price(p.total_value_idr),
            r: <ReceivedBar value={p.received_pct} />,
            x: <button className="ghost small" aria-label={`Details of ${p.po_id}`} onClick={() => onPo(p.po_id)}>Details</button>,
          }))}
        />
      )}
      <p className="muted small">Seed POs come from the plant's history and are read-only, except that they can be received.</p>
    </Card>
  );
}

function ExpediteDialog({ po, open, onClose }: { po: PurchaseOrder; open: boolean; onClose: () => void }) {
  const [date, setDate] = useState("");
  const [reason, setReason] = useState("");
  const m = useMutation();
  const current = po.promised_date ?? po.expected_date;
  const late = !!date && !!current && date >= current;
  const close = () => {
    if (m.busy) return;
    setDate("");
    setReason("");
    m.setErr(null);
    onClose();
  };
  const save = async () => {
    if (await m.run("expedite", () => post<PurchaseOrder>(`${PUR}/orders/${enc(po.po_id)}/expedite`, { new_promised_date: date, reason: reason.trim() }))) close();
  };
  return (
    <Dialog open={open} title={`Expedite ${po.po_id}`} onClose={close}>
      <p className="muted small">Promised now {po.promised_date ?? "–"}, expected {po.expected_date ?? "–"}. The new promised date must be earlier.</p>
      <div className="form-grid">
        <label>
          New promised date
          <input type="date" aria-label="New promised date" value={date} onChange={(e) => setDate(e.target.value)} className={late ? "invalid" : ""} />
          {late && <span className="field-error">The new date must be before {current}.</span>}
        </label>
        <label>
          Reason
          <input aria-label="Expedite reason" value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
      </div>
      {m.err && <p className="error" role="alert">{m.err}</p>}
      <div className="dialog-foot">
        <button className="ghost" onClick={close} disabled={!!m.busy}>Back</button>
        <button className="primary" onClick={save} disabled={!!m.busy || !date || late || !reason.trim()}>{m.busy ? "Saving…" : "Expedite"}</button>
      </div>
    </Dialog>
  );
}

/** PU-R44: one row per open line, defaulting to the open qty; up to 5% over is accepted. */
function ReceiveDialog({ po, open, onClose, onDone }: { po: PurchaseOrder; open: boolean; onClose: () => void; onDone: (msg: string) => void }) {
  const lines = (po.lines ?? []).filter((l) => (l.open_qty ?? l.qty - (l.received_qty ?? 0)) > 0);
  const openOf = (l: (typeof lines)[number]) => l.open_qty ?? l.qty - (l.received_qty ?? 0);
  const [vals, setVals] = useState<Record<string, string>>({});
  const [lot, setLot] = useState("");
  const [date, setDate] = useState("");
  const [note, setNote] = useState("");
  const m = useMutation();
  const val = (l: (typeof lines)[number]) => vals[l.line_id] ?? String(openOf(l));
  const errs = lines.map((l) => {
    const v = Number(val(l));
    const max = openOf(l) * (1 + TOLERANCE);
    if (val(l) === "" || Number.isNaN(v) || v < 0) return "Enter 0 or more.";
    if (v > max + 1e-9) return `Above the 5% tolerance (max ${qty(Math.floor(max * 100) / 100)}).`;
    return null;
  });
  const picked = lines.filter((l) => Number(val(l)) > 0);
  const close = () => {
    if (m.busy) return;
    setVals({});
    setLot("");
    setDate("");
    setNote("");
    m.setErr(null);
    onClose();
  };
  const receive = async () => {
    const body = {
      lines: picked.map((l) => ({ material_id: l.material_id, qty: Number(val(l)) })),
      ...(date ? { received_date: date } : {}),
      ...(lot.trim() ? { lot_id: lot.trim() } : {}),
      ...(note.trim() ? { note: note.trim() } : {}),
    };
    const gr = await m.run("receive", () => post<GoodsReceipt & { stock?: Any }>(`${PUR}/orders/${enc(po.po_id)}/receipts`, body));
    if (!gr) return;
    const stock: string[] = [];
    for (const l of body.lines) {
      try {
        const inv = await get<Any>(`/api/inventory?material_id=${enc(l.material_id)}`);
        const rows = listOf<Any>(inv, "rows").filter((r) => r.material_id === l.material_id);
        if (rows.length) stock.push(`${l.material_id} ${qty(rows.reduce((s, r) => s + (r.on_hand_qty ?? 0), 0))} ${rows[0].unit ?? ""}`.trim());
      } catch {
        /* the receipt is booked; the stock line is a convenience */
      }
    }
    onDone(`${gr.gr_id ?? "Receipt"} recorded for ${po.po_id}${gr.lot_id ? ` · lot ${gr.lot_id}` : ""}.${stock.length ? ` On hand now: ${stock.join("; ")}.` : ""}`);
    close();
  };
  return (
    <Dialog open={open} title={`Receive ${po.po_id}`} onClose={close} wide>
      <p className="muted small">Each line defaults to its open qty. Up to 5% above the open qty is accepted (tolerance); enter 0 to skip a line.</p>
      {lines.length === 0 ? (
        <p className="muted">Nothing is open on this PO.</p>
      ) : (
        <div className="table-wrap">
          <table aria-label="Receipt lines">
            <thead>
              <tr><th>Material</th><th className="right">Ordered</th><th className="right">Received</th><th className="right">Open</th><th className="right">Max (+5%)</th><th>Receive now</th></tr>
            </thead>
            <tbody>
              {lines.map((l, i) => (
                <tr key={l.line_id}>
                  <td><strong>{l.material_id}</strong> <span className="muted small">{l.material_name}</span></td>
                  <td className="right">{qty(l.qty)} {l.unit ?? ""}</td>
                  <td className="right">{qty(l.received_qty ?? 0)}</td>
                  <td className="right">{qty(openOf(l))}</td>
                  <td className="right">{qty(Math.floor(openOf(l) * (1 + TOLERANCE) * 100) / 100)}</td>
                  <td>
                    <input type="number" min="0" className={`qty ${errs[i] ? "invalid" : ""}`} aria-label={`Receive qty of ${l.material_id}`} value={val(l)} onChange={(e) => setVals((v) => ({ ...v, [l.line_id]: e.target.value }))} />
                    {errs[i] && <div className="field-error">{errs[i]}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="form-grid">
        <label>
          Lot id (optional)
          <input aria-label="Lot id" value={lot} onChange={(e) => setLot(e.target.value)} placeholder="generated for PP-EPDM-T20 when empty" />
        </label>
        <label>
          Received date
          <input type="date" aria-label="Received date" value={date} max={today()} onChange={(e) => setDate(e.target.value)} />
        </label>
        <label>
          Note
          <input aria-label="Receipt note" value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
      </div>
      {m.err && <p className="error" role="alert">{m.err}</p>}
      <div className="dialog-foot">
        <button className="ghost" onClick={close} disabled={!!m.busy}>Back</button>
        <button className="primary" onClick={receive} disabled={!!m.busy || errs.some(Boolean) || picked.length === 0}>{m.busy ? "Receiving…" : "Post goods receipt"}</button>
      </div>
    </Dialog>
  );
}

function DocumentDialog({ poId, open, onClose }: { poId: string; open: boolean; onClose: () => void }) {
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    setText(null);
    setErr(null);
    getBlob(`${PUR}/orders/${enc(poId)}/document`)
      .then((b) => b.text())
      .then((t) => alive && setText(t))
      .catch((e) => alive && setErr(errorText(e)));
    return () => {
      alive = false;
    };
  }, [open, poId]);
  return (
    <Dialog open={open} title={`PO document ${poId}`} onClose={onClose} wide>
      {err && <p className="error" role="alert">{err}</p>}
      {text == null && !err && <p className="muted">Loading the document…</p>}
      {text != null && (
        <>
          <pre className="notice" aria-label="PO document text">{text}</pre>
          <div className="dialog-foot">
            <button onClick={() => navigator.clipboard?.writeText(text)}>Copy</button>
            <button onClick={() => window.print()}>Print</button>
          </div>
        </>
      )}
    </Dialog>
  );
}

function OrderDetail({ poId, onBack, onPr }: { poId: string; onBack: () => void; onPr: (id: string) => void }) {
  const { data: po, error, loading } = useData<PurchaseOrder>(`${PUR}/orders/${enc(poId)}`);
  const m = useMutation();
  const [dialog, setDialog] = useState<"expedite" | "receive" | "document" | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 8000);
    return () => window.clearTimeout(t);
  }, [toast]);
  const act = (action: string) => m.run(action, () => post<PurchaseOrder>(`${PUR}/orders/${enc(poId)}/${action}`, {}));
  const back = <button className="ghost small" onClick={onBack}>← All purchase orders</button>;
  const toastBox = toast && (
    <div className="pur-toast" role="status">
      {toast} <button className="ghost small" aria-label="Dismiss" onClick={() => setToast(null)}>✕</button>
    </div>
  );
  if (error) return <Card title={poId} actions={back}><p className="error" role="alert">{error}</p></Card>;
  if (!po || po.po_id !== poId) return <>{toastBox}<Card title={poId} actions={back}><p className="muted">{loading ? "Loading the purchase order…" : "Not found."}</p></Card></>;
  const s = po.status;
  const seed = po.origin === "seed";
  const receivedAny = (po.lines ?? []).some((l) => (l.received_qty ?? 0) > 0) || (po.receipts ?? []).length > 0;
  const receivable = s === "Issued" || s === "Partially Received";
  const btn = (label: string, action: string, primary = false) => (
    <button className={primary ? "primary" : ""} disabled={!!m.busy} onClick={() => act(action)}>{m.busy === action ? `${label}…` : label}</button>
  );
  return (
    <>
      {toastBox}
      <Card title={<>{po.po_id} <PoBadge status={s} /> {seed && <Badge tone="info">seed</Badge>} {po.overdue && <Badge tone="bad">overdue {num(po.days_late)} d</Badge>}</>} actions={back}>
        <div className="fields">
          <Field label="Vendor">{po.vendor_name ?? po.vendor_id}</Field>
          <Field label="Order date">{po.order_date ?? "–"}</Field>
          <Field label="Promised">{po.promised_date ?? "–"}</Field>
          <Field label="Expected">{po.expected_date ?? "–"}</Field>
          <Field label="Total value">{price(po.total_value_idr)}</Field>
          <Field label="Received"><ReceivedBar value={po.received_pct} /></Field>
          <Field label="Source PR">{po.pr_id ? <button className="ghost small" onClick={() => onPr(po.pr_id as string)}>{po.pr_id}</button> : "–"}</Field>
          <Field label="Note">{po.note || "–"}</Field>
        </div>
        <div className="row pur-actions">
          {!seed && s === "Draft" && btn("Issue", "issue", true)}
          {!seed && receivable && <button disabled={!!m.busy} onClick={() => setDialog("expedite")}>Expedite</button>}
          {receivable && <button className="primary" disabled={!!m.busy} onClick={() => setDialog("receive")}>Receive</button>}
          {!seed && (s === "Received" || s === "Partially Received") && btn("Close", "close")}
          {!seed && (s === "Draft" || (s === "Issued" && !receivedAny)) && btn("Cancel", "cancel")}
          <button className="ghost" onClick={() => setDialog("document")}>PO document</button>
        </div>
        {seed && <p className="muted small">Seed PO from the plant's history: read-only, but a delivery can be received.</p>}
        {m.err && <p className="error" role="alert">{m.err}</p>}
      </Card>
      <Card title="Lines">
        <Table
          cols={[{ key: "m", label: "Material" }, { key: "o", label: "Ordered", align: "right" }, { key: "r", label: "Received", align: "right" }, { key: "op", label: "Open", align: "right" }, { key: "p", label: "Unit price", align: "right" }, { key: "v", label: "Value", align: "right" }]}
          rows={(po.lines ?? []).map((l) => ({
            m: <><strong>{l.material_id}</strong> <span className="muted small">{l.material_name}</span></>,
            o: `${qty(l.qty)} ${l.unit ?? ""}`,
            r: qty(l.received_qty ?? 0),
            op: <span className={(l.open_qty ?? 0) > 0 ? "" : "muted"}>{qty(l.open_qty ?? 0)}</span>,
            p: price(l.unit_price_idr),
            v: price(l.value_idr),
          }))}
        />
      </Card>
      <Card title={`Receipts (${(po.receipts ?? []).length})`}>
        <ReceiptTable rows={po.receipts ?? []} />
      </Card>
      <Card title="History">
        <History rows={po.history} />
      </Card>
      <ExpediteDialog po={po} open={dialog === "expedite"} onClose={() => setDialog(null)} />
      <ReceiveDialog po={po} open={dialog === "receive"} onClose={() => setDialog(null)} onDone={setToast} />
      <DocumentDialog poId={po.po_id} open={dialog === "document"} onClose={() => setDialog(null)} />
    </>
  );
}

/* ---------------- Receipts ---------------- */

function ReceiptTable({ rows, onPo }: { rows: GoodsReceipt[]; onPo?: (id: string) => void }) {
  return (
    <Table
      empty="No goods receipt yet."
      cols={[{ key: "gr", label: "Receipt" }, { key: "po", label: "PO" }, { key: "d", label: "Received" }, { key: "l", label: "Lines" }, { key: "lot", label: "Lot" }, { key: "by", label: "By" }, { key: "n", label: "Note" }]}
      rows={rows.map((g) => ({
        gr: <strong>{g.gr_id}</strong>,
        po: onPo ? <button className="ghost small" onClick={() => onPo(g.po_id)}>{g.po_id}</button> : g.po_id,
        d: g.received_date ?? "–",
        l: (g.lines ?? []).map((l) => `${qty(l.qty)} ${l.material_id}`).join(", ") || "–",
        lot: g.lot_id ?? "–",
        by: g.by ?? "–",
        n: g.note ?? "",
      }))}
    />
  );
}

function ReceiptsTab({ onPo }: { onPo: (id: string) => void }) {
  const [po, setPo] = useState("");
  const { data, error, loading } = useData<Any>(`${PUR}/receipts${po.trim() ? `?po_id=${enc(po.trim())}` : ""}`);
  const rows = listOf<GoodsReceipt>(data, "receipts");
  return (
    <Card title={`Goods receipts (${rows.length})`}>
      <div className="filters">
        <label>
          PO
          <input aria-label="Filter receipts by PO" value={po} onChange={(e) => setPo(e.target.value)} placeholder="e.g. PO-2702-030" />
        </label>
      </div>
      {error ? <p className="error" role="alert">{error}</p> : !data && loading ? <p className="muted">Loading receipts…</p> : <ReceiptTable rows={rows} onPo={onPo} />}
      <p className="muted small">A receipt raises on hand and lowers the PO's incoming by what arrived.</p>
    </Card>
  );
}

/* ---------------- Vendors ---------------- */

function RecommendForm() {
  const materials = listOf<Any>(useData<Any>("/api/master/materials").data, "materials");
  const [mat, setMat] = useState("");
  const [q, setQ] = useState("");
  const [need, setNeed] = useState("");
  const [rec, setRec] = useState<VendorRecommendation | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const ok = !!mat && Number(q) > 0 && !!need;
  const go = async () => {
    setBusy(true);
    setErr(null);
    try {
      setRec(await get<VendorRecommendation>(`${PUR}/recommend?${new URLSearchParams({ material_id: mat, qty: String(Number(q)), need_by: need })}`));
    } catch (e) {
      setRec(null);
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Recommend a vendor">
      <div className="filters">
        <label>
          Material
          <select aria-label="Material to buy" value={mat} onChange={(e) => setMat(e.target.value)}>
            <option value="">Choose a material</option>
            {materials.map((x) => <option key={x.material_id} value={x.material_id}>{x.material_id}{x.material_name ? ` · ${x.material_name}` : ""}</option>)}
          </select>
        </label>
        <label>
          Qty
          <input type="number" min="0" aria-label="Qty to buy" value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
        <label>
          Need by
          <input type="date" aria-label="Needed by" value={need} onChange={(e) => setNeed(e.target.value)} />
        </label>
        <div className="row filters-actions">
          <button className="primary" onClick={go} disabled={!ok || busy}>{busy ? "Comparing…" : "Recommend"}</button>
        </div>
      </div>
      {err && <p className="error" role="alert">{err}</p>}
      {rec && (
        <>
          <Table
            empty="No vendor supplies this material."
            cols={[{ key: "v", label: "Vendor" }, { key: "a", label: "Expected arrival" }, { key: "ok", label: "In time?" }, { key: "u", label: "Unit price", align: "right" }, { key: "p", label: "PO value", align: "right" }]}
            rows={(rec.options ?? []).map((o) => ({
              v: <>{o.vendor_name} {o.vendor_id === rec.recommended_vendor_id && <Badge tone="good">recommended</Badge>}</>,
              a: `${o.expected_arrival ?? "–"}${o.expected_delay_days != null ? ` (+${o.expected_delay_days} d delay)` : ""}`,
              ok: o.arrives_before_need == null ? "–" : o.arrives_before_need ? <Badge tone="good">yes</Badge> : <Badge tone="bad">no</Badge>,
              u: price(o.unit_price_idr),
              p: price(o.po_value_idr),
            }))}
          />
          <p className="small">Recommended: <strong>{rec.recommended_vendor ?? "–"}</strong>{rec.premium_vs_cheapest_idr ? ` (premium ${formatIdr(rec.premium_vs_cheapest_idr)} vs the cheapest)` : ""}. Earliest arrival in time wins, then the lowest price.</p>
        </>
      )}
    </Card>
  );
}

function VendorList({ onVendor }: { onVendor: (id: string) => void }) {
  const { data, error, loading } = useData<Any>(`${PUR}/vendors`);
  const rows = listOf<Vendor>(data, "vendors");
  return (
    <>
      <Card title="Vendor scorecard">
        {error ? (
          <p className="error" role="alert">{error}</p>
        ) : !data && loading ? (
          <p className="muted">Loading vendors…</p>
        ) : (
          <Table
            empty="No vendor."
            cols={[{ key: "v", label: "Vendor" }, { key: "c", label: "Category" }, { key: "m", label: "Materials" }, { key: "n", label: "POs", align: "right" }, { key: "ot", label: "On time", align: "right" }, { key: "d", label: "Avg delay", align: "right" }, { key: "lt", label: "Lead time (quoted / actual)", align: "right" }, { key: "p", label: "Unit price", align: "right" }, { key: "x", label: "" }]}
            rows={rows.map((v) => ({
              v: <strong>{v.vendor_name}</strong>,
              c: v.category ?? "–",
              m: <span className="small">{(v.materials ?? []).map(materialId).join(", ") || "–"}</span>,
              n: num(v.po_count),
              ot: <OnTime value={v.on_time_pct} />,
              d: v.avg_delay_days == null ? "–" : `${v.avg_delay_days} d`,
              lt: `${leadTime(v) ?? "–"} / ${v.effective_lead_time_days ?? "–"} d`,
              p: price(v.unit_price_idr),
              x: <button className="ghost small" aria-label={`Details of ${v.vendor_name}`} onClick={() => onVendor(v.vendor_id)}>Details</button>,
            }))}
          />
        )}
      </Card>
      <RecommendForm />
    </>
  );
}

function PoMiniTable({ rows, onPo, empty }: { rows: PurchaseOrder[]; onPo: (id: string) => void; empty: string }) {
  return (
    <Table
      empty={empty}
      cols={[{ key: "po", label: "PO" }, { key: "s", label: "Status" }, { key: "e", label: "Expected" }, { key: "v", label: "Value", align: "right" }, { key: "r", label: "Received" }]}
      rows={rows.map((p) => ({
        po: <><button className="ghost small" onClick={() => onPo(p.po_id)}>{p.po_id}</button> {p.origin === "seed" && <Badge tone="info">seed</Badge>}</>,
        s: <><PoBadge status={p.status} /> {p.overdue && <Badge tone="bad">overdue</Badge>}</>,
        e: p.expected_date ?? "–",
        v: price(p.total_value_idr),
        r: <ReceivedBar value={p.received_pct} />,
      }))}
    />
  );
}

function VendorDetailView({ vendorId, onBack, onPo }: { vendorId: string; onBack: () => void; onPo: (id: string) => void }) {
  const { data: v, error, loading } = useData<VendorDetail>(`${PUR}/vendors/${enc(vendorId)}`);
  const back = <button className="ghost small" onClick={onBack}>← All vendors</button>;
  if (error) return <Card title={vendorId} actions={back}><p className="error" role="alert">{error}</p></Card>;
  if (!v) return <Card title={vendorId} actions={back}><p className="muted">{loading ? "Loading the vendor…" : "Not found."}</p></Card>;
  const sc: Partial<Vendor> = { ...v, ...(v.scorecard ?? {}) };
  const received = v.received_pos ?? v.recent_receipts ?? [];
  return (
    <>
      <Card title={v.vendor_name ?? vendorId} actions={back}>
        <div className="fields">
          <Field label="Category">{v.category ?? "–"}</Field>
          <Field label="Quoted lead time">{leadTime(sc) ?? "–"} d</Field>
          <Field label="Actual lead time">{sc.effective_lead_time_days ?? "–"} d</Field>
          <Field label="On time"><OnTime value={sc.on_time_pct} /></Field>
          <Field label="Avg delay">{sc.avg_delay_days ?? "–"} d</Field>
          <Field label="POs scored">{num(sc.po_count)}</Field>
          <Field label="Unit price">{price(v.unit_price_idr)}</Field>
        </div>
      </Card>
      <Card title="Materials supplied">
        <Table
          empty="This vendor supplies no material in the master data."
          cols={[{ key: "m", label: "Material" }, { key: "u", label: "Unit" }, { key: "p", label: "Price", align: "right" }]}
          rows={(v.materials ?? []).map((x) => (typeof x === "string" ? { m: <strong>{x}</strong>, u: "–", p: "–" } : { m: <><strong>{x.material_id}</strong> <span className="muted small">{x.material_name}</span></>, u: x.unit ?? "–", p: price(x.unit_price_idr ?? v.unit_price_idr) }))}
        />
      </Card>
      <Card title={`Open purchase orders (${(v.open_pos ?? []).length})`}>
        <PoMiniTable rows={v.open_pos ?? []} onPo={onPo} empty="No open PO with this vendor." />
      </Card>
      <Card title="Recent receipts (last 10 received POs)">
        <PoMiniTable rows={received} onPo={onPo} empty="Nothing received from this vendor yet." />
      </Card>
    </>
  );
}

/* ---------------- Page ---------------- */

export function PurchasingPage() {
  const [params, setParams] = useSearchParams();
  const tabParam = params.get("tab");
  const tab: Tab = TABS.some(([id]) => id === tabParam) ? (tabParam as Tab) : "overview";
  const go = (next: Record<string, string>) => setParams(new URLSearchParams(next));
  const onPr = (id: string) => go({ tab: "requisitions", pr: id });
  const onPo = (id: string) => go({ tab: "orders", po: id });
  const pr = params.get("pr");
  const po = params.get("po");
  const vendor = params.get("vendor");
  return (
    <>
      <section className="hero">
        <div>
          <p className="hero-tag">Module 5 · from shortage to stock</p>
          <h1>Purchasing</h1>
          <p className="hero-sub">
            A shortage becomes a requisition, an approved requisition becomes one PO per vendor, and an issued PO counts as incoming for Material Readiness and MRP until the goods
            receipt puts it on hand.
          </p>
        </div>
      </section>
      <div className="chips" role="tablist" aria-label="Purchasing views">
        {TABS.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={`chip ${tab === id ? "on" : ""}`} onClick={() => go({ tab: id })}>{label}</button>
        ))}
      </div>
      {tab === "overview" && <OverviewTab onPo={onPo} onPr={onPr} />}
      {tab === "requisitions" && (pr ? <RequisitionDetail prId={pr} onBack={() => go({ tab: "requisitions" })} onPo={onPo} /> : <RequisitionList onPr={onPr} />)}
      {tab === "orders" && (po ? <OrderDetail poId={po} onBack={() => go({ tab: "orders" })} onPr={onPr} /> : <OrderList onPo={onPo} />)}
      {tab === "receipts" && <ReceiptsTab onPo={onPo} />}
      {tab === "vendors" && (vendor ? <VendorDetailView vendorId={vendor} onBack={() => go({ tab: "vendors" })} onPo={onPo} /> : <VendorList onVendor={(id) => go({ tab: "vendors", vendor: id })} />)}
      <AskAgent module="purchasing" placeholder="Which vendor should supply the next PP-EPDM-T20 lot, and why?" />
    </>
  );
}

export default PurchasingPage;
