/**
 * Module 3, Work Orders: the shop-floor side of the plans' work orders.
 *
 * Work orders are born in the plan wizard. Once the plan is confirmed they are released, started, completed or
 * cancelled here through one state machine whose guards the server names in full, so a supervisor can fix every
 * missing condition at once. Output is reported here (the stand-in for the operator terminal), and a work order can
 * be split across machines. The detail dialog is shared with the Production Board.
 */

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, Dialog, Field, Kpi, Source, num } from "../components/ui";
import { ApiError, errorText, post } from "../lib/api";
import { useApp, useData } from "../lib/store";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;
type Tone = "neutral" | "good" | "warn" | "bad" | "info";

export const WO_TONE: Record<string, Tone> = {
  Draft: "neutral",
  Scheduled: "neutral",
  Confirmed: "info",
  Released: "info",
  "In Production": "warn",
  Running: "good",
  "At Risk": "warn",
  Delayed: "bad",
  Maintenance: "info",
  Completed: "good",
  Cancelled: "neutral",
};

export function WoStatus({ status }: { status: string }) {
  return <Badge tone={WO_TONE[status] ?? "neutral"}>{status}</Badge>;
}

export const dt = (v: string | null | undefined) => (v ? v.slice(0, 16).replace("T", " ") : "–");
const toLocal = (v: string | null | undefined) => (v ? v.slice(0, 16) : "");
const withZone = (v: string) => (v ? `${v}:00+07:00` : v); // the plant runs on WIB

function fieldErrors(e: unknown): { field: string; message: string }[] {
  const f = e instanceof ApiError ? (e.body.detail as Any)?.fields : null;
  return Array.isArray(f) ? f : [];
}

/** One work order: facts, chain, served orders, lifecycle actions with their guards, output, split, dispatch, history. */
export function WorkOrderDialog({ woId, onClose, dispatch = false }: { woId: string | null; onClose: () => void; dispatch?: boolean }) {
  const { bump } = useApp();
  const { data: w, error } = useData<Any>(woId ? `/api/work-orders/${encodeURIComponent(woId)}` : null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; tone: "bad" | "good" } | null>(null);
  const [cancelReason, setCancelReason] = useState("");
  const [out, setOut] = useState({ output_qty: "", reject_qty: "", scrap_qty: "" });
  const [parts, setParts] = useState<{ qty: string; machine_id: string }[] | null>(null);
  const [sched, setSched] = useState<{ start: string; end: string; machine: string; priority: string; reason: string } | null>(null);
  const loaded = w && w.wo_id === woId ? w : null;
  const s = sched ?? (loaded ? { start: toLocal(loaded.planned_start), end: toLocal(loaded.planned_end), machine: loaded.machine_id ?? "", priority: String(loaded.priority ?? ""), reason: "" } : null);

  const close = () => {
    setMsg(null);
    setCancelReason("");
    setOut({ output_qty: "", reject_qty: "", scrap_qty: "" });
    setParts(null);
    setSched(null);
    onClose();
  };
  const run = async (path: string, body: unknown, done: string) => {
    setBusy(true);
    setMsg(null);
    try {
      await post(path, body);
      setMsg({ text: done, tone: "good" });
      setSched(null);
      bump();
      return true;
    } catch (e) {
      const f = fieldErrors(e);
      const reasons = e instanceof ApiError ? (e.body.detail as Any)?.reasons : null;
      setMsg({ text: f.length ? f.map((x) => x.message).join(" ") : Array.isArray(reasons) ? `${e instanceof ApiError ? e.body.title : ""}: ${reasons.join(" ")}` : errorText(e), tone: "bad" });
      return false;
    } finally {
      setBusy(false);
    }
  };
  const move = (to: string, label: string) => run(`/api/work-orders/${loaded.wo_id}/transition`, { to, reason: to === "Cancelled" ? cancelReason : "" }, `${label}: done.`);
  const disp = (body: Record<string, unknown>, done: string) => run("/api/production-board/dispatch", { wo_id: loaded.wo_id, reason: s?.reason ?? "", ...body }, done);

  return (
    <Dialog open={!!woId} title={woId ? `Work order ${woId}` : "Work order"} onClose={close} wide>
      {error && <p className="error" role="alert">{error}</p>}
      {!loaded && !error && <p className="muted">Loading…</p>}
      {loaded && (
        <div className="stack">
          <div className="fields">
            <Field label="Status"><WoStatus status={loaded.status} /> {loaded.board_status !== loaded.status && <WoStatus status={loaded.board_status} />}</Field>
            <Field label="Product">{loaded.sku} · {loaded.product_name}</Field>
            <Field label="Process">{loaded.routing_step}. {loaded.process}</Field>
            <Field label="Machine / mold">{loaded.machine_id ?? "–"}{loaded.mold_id ? ` / ${loaded.mold_id}` : ""}</Field>
            <Field label="Plan"><Link to={`/planning/${loaded.plan_id}`}>{loaded.plan_number}</Link> ({loaded.plan_status})</Field>
            <Field label="Priority">{loaded.priority ?? "–"}</Field>
            <Field label="Planned">{dt(loaded.planned_start)} → {dt(loaded.planned_end)} · shift {loaded.shift ?? "–"}</Field>
            <Field label="Actual">{loaded.actual_start ? `${dt(loaded.actual_start)} → ${loaded.actual_end ? dt(loaded.actual_end) : "running"}` : "not started"}</Field>
            <Field label="Output">{num(loaded.output_qty)} good / {num(loaded.qty)} ({loaded.progress_pct}%)</Field>
            <Field label="Reject / scrap">{num(loaded.reject_qty)} / {num(loaded.scrap_qty)}</Field>
            {loaded.available_qty != null && <Field label="Handed over, not yet processed">{num(loaded.available_qty)}</Field>}
            {loaded.cancel_reason && <Field label="Cancel reason">{loaded.cancel_reason}</Field>}
          </div>

          <div className="small">
            <strong>Process chain:</strong>{" "}
            {loaded.predecessors.length ? loaded.predecessors.map((p: Any) => `${p.wo_id} (${p.process}, ${p.status}, ${num(p.output_qty)} good)`).join(", ") : "first process"} →{" "}
            <strong>{loaded.wo_id}</strong> → {loaded.successors.length ? loaded.successors.map((p: Any) => `${p.wo_id} (${p.process})`).join(", ") : "last process"}
          </div>
          {loaded.children.length > 0 && (
            <div className="notice-box info small">
              Split into {loaded.children_detail.map((c: Any) => `${c.wo_id} (${num(c.qty)} on ${c.machine_id}, ${c.status})`).join(", ")}. The parts run and report; this work order adds them up.
            </div>
          )}
          {loaded.parent_wo_id && <p className="small muted">Part of {loaded.parent_wo_id}.</p>}
          {loaded.demand.length > 0 && (
            <p className="small">
              <strong>Serves:</strong> {loaded.demand.map((d: Any) => `${d.order_number} ${d.customer_name} ${num(d.qty)} pcs due ${d.requested_delivery_date}`).join("; ")}
            </p>
          )}

          {!loaded.executing && (
            <p className="notice-box info small">
              Plan {loaded.plan_number} is {loaded.plan_status}. {["Draft", "Planning", "Ready"].includes(loaded.plan_status) ? <>Change this work order in the <Link to={`/planning/${loaded.plan_id}`}>plan wizard</Link>; it reaches the floor once the plan is confirmed.</> : "Its work orders no longer change."}
            </p>
          )}

          {loaded.executing && loaded.transitions.length > 0 && (
            <Card title="Status">
              <div className="row">
                {loaded.transitions.filter((t: Any) => t.to !== "Cancelled").map((t: Any) => (
                  <button key={t.to} className={t.allowed ? "primary" : ""} disabled={busy || !t.allowed} title={t.reasons.join(" ")} onClick={() => move(t.to, t.label)}>
                    {t.label}
                  </button>
                ))}
              </div>
              {loaded.transitions.filter((t: Any) => !t.allowed && t.to !== "Cancelled").map((t: Any) => (
                <p key={t.to} className="small muted">
                  <strong>{t.label}</strong> needs: {t.reasons.join(" ")}
                </p>
              ))}
              {loaded.transitions.some((t: Any) => t.to === "Cancelled") && (
                <div className="filters">
                  <label>
                    Cancel reason (required)
                    <input aria-label="Cancel reason" value={cancelReason} onChange={(e) => setCancelReason(e.target.value)} placeholder="at least 3 characters" />
                  </label>
                  <button className="filters-actions" disabled={busy || cancelReason.trim().length < 3} onClick={() => move("Cancelled", "Cancel")}>Cancel work order</button>
                </div>
              )}
            </Card>
          )}

          {loaded.status === "In Production" && !loaded.children.length && (
            <Card title="Report output">
              <div className="filters">
                {(["output_qty", "reject_qty", "scrap_qty"] as const).map((k) => (
                  <label key={k}>
                    {k === "output_qty" ? "Good" : k === "reject_qty" ? "Reject" : "Scrap"}
                    <input type="number" min={0} aria-label={k === "output_qty" ? "Good pieces" : k === "reject_qty" ? "Reject pieces" : "Scrap pieces"} value={out[k]} onChange={(e) => setOut({ ...out, [k]: e.target.value })} />
                  </label>
                ))}
                <button
                  className="primary filters-actions"
                  disabled={busy}
                  onClick={async () => {
                    const body = { output_qty: Number(out.output_qty || 0), reject_qty: Number(out.reject_qty || 0), scrap_qty: Number(out.scrap_qty || 0) };
                    if (await run(`/api/work-orders/${loaded.wo_id}/output`, body, "Output recorded.")) setOut({ output_qty: "", reject_qty: "", scrap_qty: "" });
                  }}
                >
                  Report
                </button>
              </div>
              <p className="small muted">Good, reject and scrap are separate buckets. A later process cannot take in more than the one before handed over.</p>
            </Card>
          )}

          {loaded.can_split && (
            <Card title="Split across machines">
              {!parts ? (
                <button disabled={busy} onClick={() => setParts([{ qty: String(Math.ceil(loaded.qty / 2)), machine_id: loaded.machine_id }, { qty: String(Math.floor(loaded.qty / 2)), machine_id: loaded.compatible_machines.find((m: string) => m !== loaded.machine_id) ?? loaded.machine_id }])}>
                  Split…
                </button>
              ) : (
                <>
                  {parts.map((p, k) => (
                    <div key={k} className="filters">
                      <label>
                        Part {String.fromCharCode(65 + k)} qty
                        <input type="number" min={1} aria-label={`Part ${k + 1} quantity`} value={p.qty} onChange={(e) => setParts(parts.map((x, j) => (j === k ? { ...x, qty: e.target.value } : x)))} />
                      </label>
                      <label>
                        Machine
                        <select aria-label={`Part ${k + 1} machine`} value={p.machine_id} onChange={(e) => setParts(parts.map((x, j) => (j === k ? { ...x, machine_id: e.target.value } : x)))}>
                          {loaded.compatible_machines.map((m: string) => <option key={m}>{m}</option>)}
                        </select>
                      </label>
                    </div>
                  ))}
                  <p className="small muted">
                    Parts must add up to {num(loaded.qty)} (now {num(parts.reduce((t, p) => t + Number(p.qty || 0), 0))}). Parts on different machines run side by side;{" "}
                    {loaded.process === "Molding" ? "molding parts share one mold, so they run one after the other." : "parts on the same machine run one after the other."}
                  </p>
                  <div className="row">
                    <button onClick={() => setParts([...parts, { qty: "0", machine_id: loaded.machine_id }])}>+ Part</button>
                    {parts.length > 2 && <button onClick={() => setParts(parts.slice(0, -1))}>− Part</button>}
                    <button
                      className="primary"
                      disabled={busy}
                      onClick={async () => {
                        if (await run(`/api/work-orders/${loaded.wo_id}/split`, { parts: parts.map((p) => ({ qty: Number(p.qty || 0), machine_id: p.machine_id })) }, "Split done.")) setParts(null);
                      }}
                    >
                      Split work order
                    </button>
                  </div>
                </>
              )}
            </Card>
          )}

          {dispatch && loaded.dispatchable && s && (
            <Card title="Dispatch">
              <div className="filters">
                <label>
                  Start
                  <input type="datetime-local" aria-label="New start" value={s.start} onChange={(e) => setSched({ ...s, start: e.target.value })} />
                </label>
                <label>
                  End
                  <input type="datetime-local" aria-label="New end" value={s.end} onChange={(e) => setSched({ ...s, end: e.target.value })} />
                </label>
                <button className="primary filters-actions" disabled={busy} onClick={() => disp({ action: "RESCHEDULE", planned_start: withZone(s.start), planned_end: withZone(s.end) }, "Rescheduled.")}>
                  Reschedule
                </button>
              </div>
              <div className="filters">
                <label>
                  Machine
                  <select aria-label="Move to machine" value={s.machine} onChange={(e) => setSched({ ...s, machine: e.target.value })}>
                    {loaded.compatible_machines.map((m: string) => <option key={m}>{m}</option>)}
                  </select>
                </label>
                <button className="filters-actions" disabled={busy || s.machine === loaded.machine_id} onClick={() => disp({ action: "REASSIGN_MACHINE", machine_id: s.machine }, `Moved to ${s.machine}.`)}>
                  Move
                </button>
                <label>
                  Priority (1 = most urgent)
                  <input type="number" min={1} max={loaded.max_priority} aria-label="Priority" value={s.priority} onChange={(e) => setSched({ ...s, priority: e.target.value })} />
                </label>
                <button className="filters-actions" disabled={busy} onClick={() => disp({ action: "REPRIORITISE", priority: Number(s.priority) }, "Priority saved.")}>
                  Save priority
                </button>
              </div>
              <div className="filters">
                <label>
                  Reason for the log (optional)
                  <input aria-label="Dispatch reason" value={s.reason} onChange={(e) => setSched({ ...s, reason: e.target.value })} />
                </label>
              </div>
              <p className="small muted">A change that would double-book a machine, or put work on a machine during its maintenance, is refused with the work order already booked there.</p>
            </Card>
          )}

          {msg && <p className={`notice-box ${msg.tone}`} role={msg.tone === "bad" ? "alert" : "status"}>{msg.text}</p>}

          {loaded.history.length > 0 && (
            <details>
              <summary className="small">History ({loaded.history.length})</summary>
              <ul className="small">
                {loaded.history.map((x: Any, k: number) => (
                  <li key={k}>
                    {dt(x.at)} · {x.by} · <strong>{x.action}</strong> {x.detail}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </Dialog>
  );
}

export function WorkOrdersPage() {
  const [status, setStatus] = useState("");
  const [process, setProcess] = useState("");
  const [planId, setPlanId] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const qs = new URLSearchParams(Object.entries({ status, process, plan_id: planId, q }).filter(([, v]) => v)).toString();
  const { data, error, loading } = useData<Any>(`/api/work-orders${qs ? `?${qs}` : ""}`);
  const counts = data?.counts ?? {};
  const rows: Any[] = useMemo(() => data?.work_orders ?? [], [data]);
  const anyFilter = !!(status || process || planId || q);
  return (
    <>
      <section className="hero">
        <div>
          <p className="hero-tag">Module 3 · MES / Shop Floor</p>
          <h1>Work Orders</h1>
          <p className="hero-sub">
            The work orders of confirmed production plans: release them to the floor, start, report output, complete, or split one across machines. Every guard the
            server checks is named before you click.
          </p>
          <div className="row">
            <Link to="/production-board" className="button">Production board</Link>
            <Link to="/planning" className="small">Production plans →</Link>
          </div>
        </div>
      </section>
      {data && (
        <div className="kpis">
          <Kpi label="Work orders" value={num(data.total)} sub={`plant clock ${dt(data.now)}`} />
          <Kpi label="In Production" value={num(counts["In Production"])} tone={counts["In Production"] ? "good" : undefined} />
          <Kpi label="Confirmed & Released" value={num((counts.Confirmed ?? 0) + (counts.Released ?? 0))} sub="waiting to start" />
          <Kpi label="Completed" value={num(counts.Completed)} sub={counts.Cancelled ? `${num(counts.Cancelled)} cancelled` : undefined} />
        </div>
      )}
      <Card title={data ? `Work orders (${rows.length}${anyFilter ? ` of ${data.total}` : ""})` : "Work orders"}>
        <div className="chips" role="group" aria-label="Filter by status">
          <button className={`chip ${status === "" ? "on" : ""}`} aria-pressed={status === ""} onClick={() => setStatus("")}>All</button>
          {(data?.statuses ?? []).map((s: string) => (
            <button key={s} className={`chip ${status === s ? "on" : ""}`} aria-pressed={status === s} onClick={() => setStatus(s)}>{s}</button>
          ))}
        </div>
        <div className="filters">
          <label>
            Process
            <select aria-label="Filter by process" value={process} onChange={(e) => setProcess(e.target.value)}>
              <option value="">All processes</option>
              {(data?.processes ?? []).map((p: string) => <option key={p}>{p}</option>)}
            </select>
          </label>
          <label>
            Plan
            <select aria-label="Filter by plan" value={planId} onChange={(e) => setPlanId(e.target.value)}>
              <option value="">All plans</option>
              {(data?.plans ?? []).map((p: Any) => <option key={p.plan_id} value={p.plan_id}>{p.plan_number} ({p.status})</option>)}
            </select>
          </label>
          <label>
            Search
            <input aria-label="Search work orders" placeholder="WO number, SKU or machine" value={q} onChange={(e) => setQ(e.target.value)} />
          </label>
        </div>
        {error ? (
          <p className="error" role="alert">Could not load work orders: {error}</p>
        ) : !data && loading ? (
          <p className="muted">Loading work orders…</p>
        ) : rows.length === 0 ? (
          anyFilter ? (
            <p className="muted">No work order matches the filter.</p>
          ) : (
            <div className="empty">
              <strong>No work orders yet</strong>
              <p className="muted">Work orders are generated in step 3 of a production plan and reach the floor once the plan is confirmed.</p>
              <Link to="/planning" className="button">Go to production plans</Link>
            </div>
          )
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Work order</th>
                  <th>Process</th>
                  <th>Machine / mold</th>
                  <th>Planned</th>
                  <th className="right">Output / target</th>
                  <th className="right">Reject</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((w) => (
                  <tr key={w.wo_id}>
                    <td>
                      <button className="ghost small" onClick={() => setOpen(w.wo_id)}><strong>{w.wo_id}</strong></button>
                      <div className="small muted">{w.sku} · {w.plan_number}{w.parent_wo_id ? ` · part of ${w.parent_wo_id}` : ""}{w.children.length ? ` · split into ${w.children.length}` : ""}</div>
                    </td>
                    <td>{w.routing_step}. {w.process}</td>
                    <td>{w.machine_id ?? "–"}{w.mold_id ? <div className="small muted">{w.mold_id}</div> : null}</td>
                    <td className="small">{dt(w.planned_start)}<div className="muted">to {dt(w.planned_end)}</div></td>
                    <td className="right">
                      <div className="progress" aria-label={`${w.progress_pct}% of target`}>
                        <span className="bar"><span style={{ width: `${Math.min(100, w.progress_pct)}%` }} /></span>
                        <span className="small">{num(w.output_qty)} / {num(w.qty)}</span>
                      </div>
                    </td>
                    <td className="right">{w.reject_qty ? <span className="risk">{num(w.reject_qty)}</span> : "0"}</td>
                    <td>
                      <WoStatus status={w.status} />
                      {!w.executing && w.status !== "Cancelled" && <div className="small muted">in the wizard</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Source>{data?.source ?? "work orders of this session's production plans"}.</Source>
      </Card>
      <WorkOrderDialog woId={open} onClose={() => setOpen(null)} />
    </>
  );
}

export default WorkOrdersPage;
