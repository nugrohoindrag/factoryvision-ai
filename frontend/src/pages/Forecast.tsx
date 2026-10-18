/** Module 1, Demand Forecast: generate immutable snapshots, read the numbers behind them, and compare with orders. */

import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AskAgent } from "../components/agents";
import { Bars, Line } from "../components/charts";
import { Badge, Card, Field, Kpi, Source, Table, num, pct } from "../components/ui";
import { ApiError, errorText, post } from "../lib/api";
import { useApp, useData } from "../lib/store";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

const WINDOWS = [30, 60, 90];
const HORIZONS = [4, 6, 8];
const signed = (v: number | null | undefined) => (v == null ? "–" : `${v > 0 ? "+" : ""}${pct(v)}`);
const isBqml = (engine: string) => engine.startsWith("BigQuery");

type FieldError = { field: string; message: string };

function fieldErrors(e: unknown): FieldError[] {
  if (!(e instanceof ApiError)) return [];
  const d = e.body.detail as { fields?: FieldError[] } | undefined;
  return d?.fields ?? [];
}

/** Sum the snapshot lines of one SKU per week (per-customer snapshots have several lines per week). */
export function weeklyTotals(lines: Any[], sku: string): { week: number; week_start: string; qty: number }[] {
  const by = new Map<string, { week: number; week_start: string; qty: number }>();
  for (const ln of lines.filter((l) => l.sku === sku)) {
    const w = by.get(ln.week_start) ?? { week: ln.week, week_start: ln.week_start, qty: 0 };
    w.qty += ln.forecast_qty;
    by.set(ln.week_start, w);
  }
  return [...by.values()].sort((a, b) => a.week_start.localeCompare(b.week_start));
}

export function insufficientText(withOrders: number, weeks: number) {
  return `Insufficient history: only ${withOrders} of ${weeks} weeks have orders`;
}

type Params = { window_days: number; horizon_weeks: number; skus: string[]; per_customer: boolean };

function GenerateForm({ products, busy, onGenerate, errors }: { products: Any[]; busy: boolean; onGenerate: (p: Params) => void; errors: FieldError[] }) {
  const [win, setWin] = useState(90);
  const [horizon, setHorizon] = useState(6);
  const [skus, setSkus] = useState<string[]>([]);
  const [perCustomer, setPerCustomer] = useState(false);
  const err = (f: string) => errors.find((e) => e.field === f)?.message;
  const toggleSku = (s: string) => setSkus((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));
  return (
    <Card title="Generate a forecast" actions={<Badge tone="info">Numbers from the forecast engine, not from Gemini</Badge>}>
      <div className="filters">
        <div>
          <span className="muted small">History window</span>
          <div className="toggle" role="group" aria-label="History window">
            {WINDOWS.map((d) => (
              <button key={d} className={win === d ? "on" : ""} aria-pressed={win === d} onClick={() => setWin(d)}>
                {d} days
              </button>
            ))}
          </div>
          {err("window_days") && <p className="field-error small">{err("window_days")}</p>}
        </div>
        <label>
          Horizon
          <select aria-label="Horizon" value={horizon} onChange={(e) => setHorizon(Number(e.target.value))}>
            {HORIZONS.map((h) => <option key={h} value={h}>{h} weeks</option>)}
          </select>
          {err("horizon_weeks") && <span className="field-error small">{err("horizon_weeks")}</span>}
        </label>
        <label className="row small">
          <input type="checkbox" checked={perCustomer} onChange={(e) => setPerCustomer(e.target.checked)} aria-label="Break down per customer" />
          Break down per customer
        </label>
      </div>
      <fieldset className="row small" aria-label="SKU filter">
        <span className="muted">SKUs:</span>
        <label className="row">
          <input type="checkbox" checked={skus.length === 0} onChange={() => setSkus([])} aria-label="All SKUs" /> All
        </label>
        {products.map((p: Any) => (
          <label key={p.sku} className="row" title={p.name}>
            <input type="checkbox" checked={skus.includes(p.sku)} onChange={() => toggleSku(p.sku)} aria-label={`Only ${p.sku}`} /> {p.sku}
          </label>
        ))}
      </fieldset>
      {err("skus") && <p className="field-error small">{err("skus")}</p>}
      {err("per_customer") && <p className="field-error small">{err("per_customer")}</p>}
      <div className="row">
        <button className="primary" disabled={busy} onClick={() => onGenerate({ window_days: win, horizon_weeks: horizon, skus, per_customer: perCustomer })}>
          {busy ? "Generating…" : "Generate forecast"}
        </button>
        <span className="muted small">
          Each run is stored as a new snapshot. Running again with the same settings supersedes the previous snapshot; its numbers never change.
        </span>
      </div>
    </Card>
  );
}

function HistoryTable({ weeks }: { weeks: Any[] }) {
  return (
    <div className="table-wrap">
      <table aria-label="Weekly order history">
        <thead>
          <tr>
            <th>Week from</th>
            {weeks.map((w) => <th key={w.week_start} className="right">{w.week_start.slice(5)}{w.days < 7 ? ` (${w.days} d)` : ""}</th>)}
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Ordered qty</td>
            {weeks.map((w) => <td key={w.week_start} className={`right ${w.qty === 0 ? "muted" : ""}`}>{num(w.qty)}</td>)}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function SnapshotDetail({ id, products, customers }: { id: string; products: Any[]; customers: Any[] }) {
  const { bump } = useApp();
  const detail = useData<Any>(`/api/forecasts/${id}`);
  const d = detail.data && detail.data.forecast_id === id ? detail.data : null;
  const [pick, setPick] = useState("FB-ARKA");
  const sku = d && !d.skus.includes(pick) ? d.skus[0] : pick;
  const vs = useData<Any>(d ? `/api/forecasts/${id}/vs-orders?sku=${sku}` : null);
  const cmp = useData<Any>(d ? `/api/forecasts/${id}/comparison` : null);
  const acc = useData<Any>(d ? "/api/forecasts/accuracy" : null);
  const name = (s: string) => products.find((p: Any) => p.sku === s)?.name;
  const custName = (c: string) => customers.find((x: Any) => x.customer_id === c)?.customer_name ?? c;
  const weekly = useMemo(() => (d ? weeklyTotals(d.lines, sku) : []), [d, sku]);

  if (detail.error)
    return (
      <div className="notice-box bad" role="alert">
        Could not load {id}: {detail.error} <button onClick={bump}>Retry</button>
      </div>
    );
  if (!d) return <p className="muted">Loading {id}…</p>;
  const s = d.sku_summary[sku];
  const v = vs.data?.forecast_id === id && vs.data?.sku === sku ? vs.data : null;
  const po = v?.customer_po_change_pct;
  const insufficient = d.insufficient_skus.length;
  const custLines = d.per_customer ? d.lines.filter((l: Any) => l.sku === sku) : [];
  const custRows = [...new Set<string>(custLines.map((l: Any) => l.customer_id).filter(Boolean))].map((c) => {
    const ls = custLines.filter((l: Any) => l.customer_id === c);
    return { c, total: ls.reduce((a: number, l: Any) => a + l.forecast_qty, 0), first: ls[0] };
  });
  const custTotal = custRows.reduce((a, r) => a + r.total, 0);
  return (
    <>
      <Card title={`Snapshot ${d.forecast_id}`} actions={<Badge tone={d.status === "Generated" ? "good" : "neutral"}>{d.status}</Badge>}>
        <div className="po-head">
          <Field label="Forecast period">{d.period_start} → {d.period_end}</Field>
          <Field label="History window">{d.window_days} days ({d.history_start} → {d.history_end})</Field>
          <Field label="Horizon">{d.horizon_weeks} weeks</Field>
          <Field label="Engine">{isBqml(d.engine) ? "BigQuery ML ARIMA_PLUS" : "Local model (dev)"}</Field>
          <Field label="Generated">{d.generated_at.replace("T", " ").slice(0, 16)} by {d.generated_by}</Field>
          <Field label="Scope">{d.skus.length === products.length ? "All SKUs" : d.skus.join(", ")}{d.per_customer ? ", per customer" : ""}</Field>
          <Field label="Forecast lines">{num(d.line_count)}</Field>
        </div>
        {d.used_by_plans.length > 0 && (
          <p className="notice-box info" role="status">
            Used by Production Plan: <strong>{d.used_by_plans.join(", ")}</strong>. This snapshot will not change, even when the forecast is generated again.
          </p>
        )}
        {d.status === "Superseded" && (
          <p className="notice-box warn" role="status">
            Superseded by <strong>{d.superseded_by}</strong>{d.superseded_at ? ` on ${d.superseded_at.slice(0, 10)}` : ""}. Its numbers are kept exactly as they were generated.
          </p>
        )}
      </Card>

      <div className="kpis">
        <Kpi label={`${sku} in ${d.horizon_weeks} weeks`} value={signed(s.change_by_final_week_pct)} sub={`vs last 7 days (${num(s.last7_daily_mean)} / day)`} tone={s.change_by_final_week_pct > 20 ? "warn" : undefined} />
        <Kpi label="Peak week" value={`Week ${s.peak_week}`} sub={signed(s.peak_change_pct)} />
        <Kpi label="Customer PO change" value={po ? signed(po) : "none"} sub={po ? "approved revised schedule" : <Link to="/orders">read a customer PO</Link>} tone={po ? "bad" : undefined} />
        <Kpi label="Firm orders cover" value={v ? pct(v.forecast_total ? (100 * v.firm_total) / v.forecast_total : null) : "–"} sub={v ? `${num(v.firm_total)} of ${num(v.forecast_total)} forecast` : undefined} />
        <Kpi label="Insufficient history" value={`${insufficient} SKU${insufficient === 1 ? "" : "s"}`} sub="numbers still produced, but flagged" tone={insufficient ? "warn" : "good"} />
        <Kpi label="Snapshot status" value={d.status} sub={d.status === "Superseded" ? `replaced by ${d.superseded_by}` : "current for these settings"} />
      </div>

      <Card
        title={`${sku} — ${name(sku) ?? sku}: weekly demand, history and forecast`}
        actions={
          <>
            <label className="row small">
              SKU
              <select value={sku} onChange={(e) => setPick(e.target.value)} aria-label="SKU">
                {d.skus.map((x: string) => <option key={x} value={x}>{x} — {name(x) ?? x}</option>)}
              </select>
            </label>
            <Badge tone="info">{isBqml(s.engine) ? "BigQuery ML ARIMA_PLUS" : "Local model (dev)"}</Badge>
          </>
        }
      >
        {s.insufficient_history && <p><Badge tone="warn">{insufficientText(s.history_weeks_with_orders, s.history_weeks)}</Badge></p>}
        <Line label={`${sku} weekly demand`} history={d.history[sku].map((w: Any) => ({ x: w.week_start, y: w.qty }))} forecast={weekly.map((w) => ({ x: w.week_start, y: w.qty }))} />
        <HistoryTable weeks={d.history[sku]} />
        <p className="small">
          History: {num(s.history_total)} ordered in {d.window_days} days (average {num((7 * s.history_total) / d.window_days)} / week); forecast: <strong>{num(s.forecast_total)}</strong> in {d.horizon_weeks} weeks. Actual orders for the period: see Forecast vs actual orders below.
        </p>
        <Table
          cols={[{ key: "wk", label: "Forecast week" }, ...weekly.map((w) => ({ key: w.week_start, label: `W${w.week} · ${w.week_start.slice(5)}`, align: "right" as const }))]}
          rows={[{ wk: "Forecast qty", ...Object.fromEntries(weekly.map((w) => [w.week_start, <strong key={w.week_start}>{num(w.qty)}</strong>])) }]}
        />
        <p className="small">How it works: {d.method}</p>
        <p className="muted small">{d.rules}</p>
        <ul className="muted small">{d.calendar_notes.map((n: string) => <li key={n}>{n}</li>)}</ul>
        <Source>{d.source}; forecast: {s.engine}</Source>
      </Card>

      {d.per_customer && (
        <Card title={`${sku} per customer`}>
          <Table
            empty="No customer ordered this SKU in the window."
            cols={[{ key: "c", label: "Customer" }, { key: "share", label: "Share", align: "right" }, { key: "fc", label: "Forecast", align: "right" }, { key: "hist", label: "History" }]}
            rows={custRows.map((r) => ({
              c: custName(r.c),
              share: pct(custTotal ? (100 * r.total) / custTotal : null, 0),
              fc: num(r.total),
              hist: r.first.insufficient_history ? <Badge tone="warn">{insufficientText(r.first.history_weeks_with_orders, r.first.history_weeks)}</Badge> : `${r.first.history_weeks_with_orders} of ${r.first.history_weeks} weeks with orders`,
            }))}
          />
          <p className="muted small">The SKU forecast is split by each customer's share of the orders in the window; the customer lines of a week add up to the SKU total.</p>
        </Card>
      )}

      <Card title="Forecast vs customer orders">
        {vs.error ? (
          <p className="error" role="alert">{vs.error}</p>
        ) : !v ? (
          <p className="muted">Loading…</p>
        ) : (
          <>
            <Table
              cols={[
                { key: "wk", label: "Week" },
                { key: "fc", label: "Forecast", align: "right" },
                { key: "firm", label: "Firm orders", align: "right" },
                { key: "sch", label: "Customer schedule", align: "right" },
                { key: "rev", label: "Revised by PO", align: "right" },
                { key: "gap", label: "Not yet ordered", align: "right" },
              ]}
              rows={v.weeks.map((w: Any) => ({
                wk: `W${w.week} · ${w.week_start}`,
                fc: <strong>{num(w.forecast_qty)}</strong>,
                firm: `${num(w.firm_qty)} (${pct(w.firm_coverage_pct, 0)})`,
                sch: num(w.schedule_qty),
                rev: w.revised_schedule_qty != null && w.revised_schedule_qty !== w.schedule_qty ? <Badge tone="warn">{num(w.revised_schedule_qty)}</Badge> : num(w.revised_schedule_qty),
                gap: num(w.uncovered_qty),
              }))}
            />
            <p className="muted small">{v.note} An approved PO revises the customer schedule only; the statistical forecast in this snapshot stays as generated.</p>
            <Source>{v.source}</Source>
          </>
        )}
      </Card>

      <Card title={`All SKUs: change by week ${d.horizon_weeks}`}>
        <Bars items={d.skus.map((x: string) => ({ label: d.sku_summary[x].insufficient_history ? `${x} ⚠` : x, value: d.sku_summary[x].change_by_final_week_pct ?? 0, tone: d.sku_summary[x].change_by_final_week_pct > 20 ? "warn" : undefined }))} unit="%" />
      </Card>

      <div className="grid2">
        <Card title="Forecast vs actual orders">
          {cmp.error ? (
            <p className="error" role="alert">{cmp.error}</p>
          ) : cmp.data?.forecast_id === id ? (
            <>
              <Table
                cols={[{ key: "s", label: "SKU" }, { key: "f", label: "Forecast", align: "right" }, { key: "a", label: "Actual orders", align: "right" }, { key: "v", label: "Variance", align: "right" }]}
                rows={cmp.data.rows.map((r: Any) => ({
                  s: <>{r.sku}{r.insufficient_history && <> <Badge tone="warn">thin history</Badge></>}</>,
                  f: num(r.forecast_qty),
                  a: num(r.actual_qty),
                  v: <span className={r.variance_qty < 0 ? "neg" : "pos"}>{num(r.variance_qty)} ({signed(r.variance_pct)})</span>,
                }))}
              />
              <p className="muted small">{cmp.data.note}</p>
            </>
          ) : (
            <p className="muted">Loading…</p>
          )}
        </Card>
        <Card title="Forecast accuracy (4-week backtest)">
          {acc.error ? (
            <p className="error" role="alert">{acc.error}</p>
          ) : acc.data ? (
            <>
              <Table
                cols={[{ key: "s", label: "SKU" }, { key: "m", label: "MAPE", align: "right" }, { key: "b", label: "Bias", align: "right" }, { key: "t", label: "Forecast / actual", align: "right" }]}
                rows={acc.data.rows.map((r: Any) => ({ s: r.sku, m: pct(r.mape_pct), b: signed(r.bias_pct), t: `${num(r.forecast_total)} / ${num(r.actual_total)}` }))}
              />
              <p className="muted small">{acc.data.method}</p>
            </>
          ) : (
            <p className="muted">Loading…</p>
          )}
        </Card>
      </div>

      <AskAgent module="forecast" placeholder={`Why is ${sku} demand changing, and what does the OEM-A PO change?`} />
    </>
  );
}

export function ForecastPage() {
  const { bump } = useApp();
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [genError, setGenError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const list = useData<Any>(`/api/forecasts${status ? `?status=${status}` : ""}`);
  const snaps: Any[] = list.data?.snapshots ?? [];

  // Open the newest Generated snapshot unless the user picked one that is still listed.
  useEffect(() => {
    if (!list.data) return;
    if (selected && snaps.some((x) => x.forecast_id === selected)) return;
    setSelected((snaps.find((x) => x.status === "Generated") ?? snaps[0])?.forecast_id ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list.data]);

  const generate = async (p: Params) => {
    setBusy(true);
    setGenError(null);
    setNotice(null);
    try {
      const snap = await post<Any>("/api/forecasts", { ...p, skus: p.skus.length ? p.skus : null });
      setNotice(
        `${snap.forecast_id} generated with ${snap.line_count} lines.` +
          (snap.supersedes.length ? ` ${snap.supersedes.join(", ")} is now Superseded; its numbers do not change.` : ""),
      );
      setStatus("");
      setSelected(snap.forecast_id);
      bump();
    } catch (e) {
      setGenError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Demand Forecast</h1>
          <p className="muted small">
            Weekly demand per SKU from customer orders by requested delivery date. {list.data?.rules ?? "Cancelled orders are excluded, days without orders count as zero, and today is not part of the history."}
          </p>
        </div>
      </div>

      <GenerateForm products={list.data?.products ?? []} busy={busy} onGenerate={generate} errors={fieldErrors(genError)} />
      {genError != null && <p className="error" role="alert">Could not generate the forecast: {errorText(genError)}</p>}
      {notice && <p className="notice-box good" role="status">{notice}</p>}

      {list.error ? (
        <div className="notice-box bad" role="alert">
          <p>Could not load forecasts: {list.error}</p>
          <button onClick={bump}>Retry</button>
        </div>
      ) : !list.data ? (
        <p className="muted">Loading forecasts…</p>
      ) : (
        <Card
          title={`Snapshots (${snaps.length})`}
          actions={
            <label className="row small">
              Status
              <select aria-label="Filter snapshots by status" value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="">All statuses</option>
                {(list.data.statuses ?? []).map((x: string) => <option key={x}>{x}</option>)}
              </select>
            </label>
          }
        >
          {snaps.length === 0 ? (
            status ? (
              <p className="muted">
                No {status} snapshot. <button className="ghost small" onClick={() => setStatus("")}>Show all</button>
              </p>
            ) : (
              <div>
                <p><strong>No forecast snapshot yet.</strong></p>
                <p className="muted">Pick a window and horizon above, then generate. The result is stored as a snapshot that a Production Plan can point to.</p>
                <button className="primary" disabled={busy} onClick={() => generate({ window_days: 90, horizon_weeks: 6, skus: [], per_customer: false })}>
                  Generate forecast
                </button>
              </div>
            )
          ) : (
            <div className="row" role="group" aria-label="Forecast snapshots">
              {snaps.map((x) => (
                <button
                  key={x.forecast_id}
                  className={`small ${selected === x.forecast_id ? "primary" : ""}`}
                  aria-pressed={selected === x.forecast_id}
                  title={`${x.window_days}-day window, ${x.horizon_weeks} weeks, ${x.skus.length} SKU(s)${x.per_customer ? ", per customer" : ""}; by ${x.generated_by}`}
                  onClick={() => setSelected(x.forecast_id)}
                >
                  {x.forecast_id} · {x.status}
                  {x.insufficient_skus.length > 0 ? " ⚠" : ""}
                </button>
              ))}
            </div>
          )}
        </Card>
      )}

      {selected && list.data && <SnapshotDetail id={selected} products={list.data.products} customers={list.data.customers ?? []} />}
    </>
  );
}
