/**
 * Capacity plan: can this period's demand be met?
 *
 * Every figure carries its formula, because a planner who cannot check a number will not act on it. Machines the
 * server could not compute get a panel of their own: a machine without a cycle time is a master-data gap, not
 * zero capacity, and treating it as zero is how a Capacity Up request lands on a machine that works fine.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, Kpi, num, pct } from "../components/ui";
import { errorText, post, put } from "../lib/api";
import { useApp, useData } from "../lib/store";
import type { CapacityStatus } from "../lib/types";
import { CapacityBadge } from "./Planning";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

const DEFAULT_FORMULAS: Record<string, string> = {
  total: "Σ (available minutes of compatible machines ÷ cycle time)",
  planning: "Total × planning utilization",
  buffer: "Total − Planning",
  utilization_pct: "Demand ÷ Total",
  gap: "max(Demand − Total, 0)",
};

export type CapacityRow = {
  sku: string;
  product_name?: string;
  demand: number;
  total: number;
  planning: number;
  buffer: number;
  utilization_pct: number;
  gap: number;
  status: CapacityStatus;
  uncomputed: { machine_id: string; reason: string }[];
};

/** Rows as the capacity API sends them; tolerant of `demand_qty` vs `demand` and a nested `capacity`. */
export function normalizeRow(r: Any): CapacityRow {
  const c = r.capacity ?? r;
  return {
    sku: r.sku,
    product_name: r.product_name,
    demand: r.demand ?? r.demand_qty ?? 0,
    total: c.total ?? 0,
    planning: c.planning ?? 0,
    buffer: c.buffer ?? 0,
    utilization_pct: c.utilization_pct ?? 0,
    gap: c.gap ?? 0,
    status: c.status ?? r.capacity_status,
    uncomputed: c.uncomputed ?? r.uncomputed ?? [],
  };
}

const monthEnd = (start: string) => {
  const d = new Date(`${start.slice(0, 7)}-01T00:00:00Z`);
  d.setUTCMonth(d.getUTCMonth() + 1, 0);
  return d.toISOString().slice(0, 10);
};

export default function CapacityPage() {
  const { bump } = useApp();
  const summary = useData<Any>("/api/modules/planning/summary");
  const base = (summary.data?.current?.month ?? new Date().toISOString()).slice(0, 7);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const s = start || `${base}-01`;
  const e = end || monthEnd(`${base}-01`);
  const ready = !!summary.data || !!summary.error;
  const cap = useData<Any>(ready && e >= s ? `/api/capacity?period_start=${s}&period_end=${e}` : null);
  const snaps = useData<Any>("/api/capacity/snapshots");
  const settings = useData<Any>("/api/capacity/settings");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; bad: boolean } | null>(null);
  const [utilDraft, setUtilDraft] = useState<string | null>(null);
  const savedUtil: number | undefined = settings.data?.planning_utilization_pct;
  const draft = utilDraft ?? (savedUtil != null ? String(savedUtil) : "");
  const draftNum = Number(draft);
  const utilInvalid = draft.trim() === "" || !Number.isFinite(draftNum) || draftNum <= 0 || draftNum > 100;

  const toggleStrict = async (value: boolean) => {
    setBusy(true);
    setNotice(null);
    try {
      await put<Any>("/api/capacity/settings", { strict_process_sequence: value });
      setNotice({ text: `Strict process sequence ${value ? "on" : "off"}.`, bad: false });
      bump();
    } catch (x) {
      setNotice({ text: errorText(x), bad: true });
    } finally {
      setBusy(false);
    }
  };

  const saveUtil = async () => {
    setBusy(true);
    setNotice(null);
    try {
      await put<Any>("/api/capacity/settings", { planning_utilization_pct: draftNum });
      setUtilDraft(null);
      setNotice({ text: `Planning utilization set to ${draftNum}%. New calculations use it; saved snapshots keep the value they were calculated with.`, bad: false });
      bump();
    } catch (x) {
      setNotice({ text: errorText(x), bad: true });
    } finally {
      setBusy(false);
    }
  };

  const recalc = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const snap = await post<Any>("/api/capacity/recalculate", { period_start: s, period_end: e });
      setNotice({ text: `Capacity snapshot ${snap.snapshot_id ?? snap.capacity_id ?? snap.number ?? ""} saved; the previous one is Superseded and its numbers stay as they were.`, bad: false });
      bump();
    } catch (x) {
      setNotice({ text: errorText(x), bad: true });
    } finally {
      setBusy(false);
    }
  };

  const rows: CapacityRow[] = (cap.data?.rows ?? []).map(normalizeRow);
  const k = cap.data?.kpis ?? {};
  const f = { ...DEFAULT_FORMULAS, ...(cap.data?.formulas ?? {}) };
  const util = cap.data?.utilization_pct ?? cap.data?.planning_utilization_pct;
  const demand = k.demand ?? rows.reduce((a, r) => a + r.demand, 0);
  const uncomputed = rows.flatMap((r) => r.uncomputed.map((u) => ({ ...u, sku: r.sku })));
  const gaps = rows.filter((r) => r.status === "Capacity Up Required");
  const list: Any[] = snaps.data?.snapshots ?? [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Capacity Plan</h1>
          <p className="muted">Capacity comes from shifts, compatible machines and cycle times, never from a typed number. <Link to="/planning">Production plans →</Link></p>
        </div>
      </div>
      <Card>
        <div className="filters">
          <label>
            Period start
            <input type="date" aria-label="Period start" value={s} onChange={(x) => setStart(x.target.value)} />
          </label>
          <label>
            Period end
            <input type="date" aria-label="Period end" value={e} onChange={(x) => setEnd(x.target.value)} className={e < s ? "invalid" : ""} />
            {e < s && <span className="field-error">The period end cannot be before the start.</span>}
          </label>
          <div className="row filters-actions">
            <button className="primary" onClick={recalc} disabled={busy || e < s}>{busy ? "Calculating…" : "Recalculate & save snapshot"}</button>
          </div>
        </div>
        <div className="filters">
          <label>
            Planning utilization (%)
            <input
              type="number"
              min={1}
              max={100}
              step={1}
              aria-label="Planning utilization"
              value={draft}
              onChange={(x) => setUtilDraft(x.target.value)}
              className={utilInvalid ? "invalid" : ""}
            />
            {utilInvalid ? (
              <span className="field-error">Use a percentage above 0 and at most 100.</span>
            ) : (
              <span className="muted small">Planning capacity = Total × this value. Default {settings.data?.default_planning_utilization_pct ?? 80}%.</span>
            )}
          </label>
          <div className="row filters-actions">
            <button className="secondary" onClick={saveUtil} disabled={busy || utilInvalid || draftNum === savedUtil}>Save utilization</button>
          </div>
          <label className="row">
            <input
              type="checkbox"
              aria-label="Strict process sequence"
              checked={!!settings.data?.strict_process_sequence}
              disabled={busy || !settings.data}
              onChange={(x) => toggleStrict(x.target.checked)}
            />
            Strict process sequence: a work order cannot start until the earlier process is completed
          </label>
        </div>
        {notice && <div className={`notice-box ${notice.bad ? "bad" : "good"}`} role={notice.bad ? "alert" : "status"}>{notice.text}</div>}
      </Card>
      {cap.error ? (
        <div className="notice-box bad" role="alert">
          Could not load the capacity plan: {cap.error} <button className="small" onClick={bump}>Retry</button>
        </div>
      ) : !cap.data ? (
        <p className="muted">Loading capacity…</p>
      ) : rows.length === 0 ? (
        <div className="empty">
          <strong>No demand in this period</strong>
          <p className="muted">Capacity is computed per product that has demand. Add customer orders or pick another period.</p>
        </div>
      ) : (
        <>
          <div className="kpis kpis5">
            <Kpi label="Total capacity" value={num(k.total)} sub={f.total} />
            <Kpi label="Planning capacity" value={num(k.planning)} sub={util != null ? `Total × ${util}%` : f.planning} />
            <Kpi label="Capacity buffer" value={num(k.buffer)} sub={f.buffer} />
            <Kpi
              label="Capacity utilization"
              value={pct(k.utilization_pct)}
              sub={`Demand ${num(demand)} ÷ Total ${num(k.total)}`}
              tone={k.utilization_pct > 100 ? "bad" : k.utilization_pct > (util ?? 80) ? "warn" : "good"}
            />
            <Kpi label="Capacity gap" value={num(k.gap)} sub={f.gap} tone={k.gap > 0 ? "bad" : "good"} />
          </div>
          {list[0] && (
            <p className="muted small">
              Latest snapshot <strong>{list[0].snapshot_id ?? list[0].capacity_id ?? list[0].number}</strong> · period {list[0].period_start} → {list[0].period_end} · utilization{" "}
              {list[0].planning_utilization_pct ?? "–"}%. A snapshot keeps the numbers that held when it was calculated.
              {savedUtil != null && list[0].planning_utilization_pct != null && savedUtil !== list[0].planning_utilization_pct && (
                <> The setting is now {savedUtil}%; recalculate to use it.</>
              )}
            </p>
          )}
          {gaps.length > 0 && (
            <div className="notice-box bad" role="status">
              Capacity gap on {gaps.map((g) => `${g.sku} (${num(g.gap)} short)`).join(", ")}: demand exceeds total capacity. Material, Purchasing and Delivery are notified; lower the planned
              quantity or add capacity.
            </div>
          )}
          {uncomputed.length > 0 && (
            <Card title={`Capacity not computed (${uncomputed.length} machine${uncomputed.length === 1 ? "" : "s"})`} className="warn-rail">
              <p className="muted small">These machines are left out and are <strong>not</strong> counted as zero capacity. Complete their master data so capacity reflects the plant.</p>
              <ul>{uncomputed.map((u, i) => <li key={i}><strong>{u.machine_id}</strong>: {u.reason} <em className="muted">({u.sku})</em></li>)}</ul>
            </Card>
          )}
          <Card title="Capacity per product">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Product</th>
                    <th className="right">Demand</th>
                    <th className="right">Total</th>
                    <th className="right">Planning</th>
                    <th className="right">Buffer</th>
                    <th className="right">Utilization</th>
                    <th className="right">Gap</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.sku}>
                      <td><strong>{r.sku}</strong>{r.product_name && <div className="muted small">{r.product_name}</div>}</td>
                      <td className="right">{num(r.demand)}</td>
                      <td className="right">{num(r.total)}</td>
                      <td className="right">{num(r.planning)}</td>
                      <td className="right">{num(r.buffer)}</td>
                      <td className="right">{pct(r.utilization_pct)}</td>
                      <td className={`right ${r.gap > 0 ? "neg" : ""}`}>{num(r.gap)}</td>
                      <td><CapacityBadge status={r.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="muted small">Within Plan: demand ≤ planning capacity · Additional Demand: still fits in the buffer · Capacity Up Required: demand exceeds total capacity. The status is set by the system.</p>
          </Card>
        </>
      )}
      <Card title={`Capacity snapshots (${list.length})`}>
        {snaps.error ? (
          <p className="error" role="alert">Could not load snapshots: {snaps.error}</p>
        ) : list.length === 0 ? (
          <p className="muted">No snapshot saved yet. Recalculate to save one; each recalculation supersedes the previous snapshot without changing its numbers.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Snapshot</th><th>Period</th><th>Status</th><th className="right">Utilization</th><th className="right">Gap</th><th>Calculated</th></tr></thead>
              <tbody>
                {list.map((x, i) => (
                  <tr key={x.snapshot_id ?? x.capacity_id ?? i}>
                    <td><strong>{x.snapshot_id ?? x.capacity_id ?? x.number ?? "–"}</strong></td>
                    <td>{x.period_start} → {x.period_end}</td>
                    <td>{x.status ?? "–"}</td>
                    <td className="right">{pct(x.kpis?.utilization_pct ?? x.utilization_pct)}</td>
                    <td className="right">{num(x.kpis?.gap ?? x.gap)}</td>
                    <td>{(x.computed_at ?? x.created_at ?? x.calculated_at ?? "").slice(0, 16).replace("T", " ") || "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
