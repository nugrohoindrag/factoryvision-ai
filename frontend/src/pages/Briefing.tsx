import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, Kpi, Source, Status, pct } from "../components/ui";
import { errorText, post } from "../lib/api";
import { formatIdr, formatIdrCompact } from "../lib/format";
import { useApp, useData } from "../lib/store";

type Briefing = {
  as_of: string;
  yesterday: string;
  oee: { molding: { oee_pct: number; availability_pct: number; performance_pct: number; quality_pct: number }; plant: { oee_pct: number }; worst_machine: string; worst_machine_oee_pct: number };
  top_losses: { id: string; title: string; loss_idr: number; detail: string; kind: string }[];
  risks: { module: string; severity: string; title: string; detail: string }[];
};

const SEVERITY_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2 };
const MODULE_PATH: Record<string, string> = { mes: "/mes", material: "/material", delivery: "/delivery", quality: "/quality" };

type Period = { from: string; to: string; scrap_kg: number; scrap_rate_pct: number | null; scrap_idr: number; kwh: number; energy_idr: number; co2e_kg: number; kwh_per_good_unit: number | null };
type Totals = { rupiah_idr: number; scrap_kg: number; kwh: number; co2e_kg: number; count: number };
type SustainabilityReport = { window_days: number; current: Period; change_pct: Record<string, number | null>; decisions: { approved: Totals; pending: Totals }; source: string };

const trend = (v: number | null | undefined) => (v == null ? "" : `${v > 0 ? "+" : ""}${v}% vs previous 4 weeks`);

/** Waste reduction: scrap, energy and CO2e of the last four weeks, and what the agents' decisions save. */
function Sustainability() {
  const { data } = useData<SustainabilityReport>("/api/sustainability");
  const [sent, setSent] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!data) return null;
  const c = data.current;
  const ap = data.decisions.approved;
  const pe = data.decisions.pending;
  const email = async () => {
    setBusy(true);
    try {
      const r = await post<{ status: string; reason: string | null }>("/api/outbox/sustainability");
      setSent(r.status === "sent" ? "Sent to management." : `In the Outbox (${r.reason ?? r.status}).`);
    } catch (e) {
      setSent(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title={`Waste and energy · ${c.from} to ${c.to}`} actions={<button disabled={busy} onClick={email}>Email to management</button>}>
      <div className="kpis">
        <Kpi label="Molding scrap" value={`${c.scrap_kg.toLocaleString()} kg`} sub={`${c.scrap_rate_pct ?? "–"}% · ${formatIdrCompact(c.scrap_idr)} resin · ${trend(data.change_pct.scrap_kg)}`} tone={(data.change_pct.scrap_kg ?? 0) > 5 ? "bad" : "good"} />
        <Kpi label="Electricity" value={`${c.kwh.toLocaleString()} kWh`} sub={`${formatIdrCompact(c.energy_idr)} · ${trend(data.change_pct.kwh)}`} />
        <Kpi label="CO2e" value={`${Math.round(c.co2e_kg / 1000).toLocaleString()} t`} sub={trend(data.change_pct.co2e_kg)} />
        <Kpi label="Saved by approved decisions / month" value={`${ap.scrap_kg.toLocaleString()} kg scrap`} sub={`${ap.kwh.toLocaleString()} kWh · ${ap.co2e_kg.toLocaleString()} kg CO2e · ${ap.count} decision(s); ${pe.count} waiting (${pe.scrap_kg.toLocaleString()} kg)`} tone={ap.count ? "good" : undefined} />
      </div>
      {sent && <p className="muted small">{sent}</p>}
      <Source>{data.source}</Source>
    </Card>
  );
}

export default function BriefingPage() {
  const { data, error, loading } = useData<Briefing>("/api/briefing");
  const { startDisruption, run, live } = useApp();
  const lossTotal = Math.max(1, data?.top_losses.reduce((a, l) => a + l.loss_idr, 0) ?? 0);
  const risks = [...(data?.risks ?? [])].sort((a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9));
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Plant Briefing</h1>
          <p className="muted">Vidad bumper plant · Monday {data?.as_of ?? "…"} 07:00 WIB · numbers for {data?.yesterday ?? "yesterday"}</p>
        </div>
        <div className="row">
          <Link to="/outbox">Email this briefing</Link>
          <button className="primary big" onClick={startDisruption} disabled={run.status === "running"}>
            {run.status === "running" ? "Agents working…" : `Run Disruption Response (${live ? "Live" : "Replay"})`}
          </button>
        </div>
      </div>
      <Status loading={loading} error={error} />
      {data && (
        <>
          <div className="kpis">
            <Kpi label="OEE molding (yesterday)" value={pct(data.oee.molding.oee_pct)} sub={`A ${pct(data.oee.molding.availability_pct)} · P ${pct(data.oee.molding.performance_pct)} · Q ${pct(data.oee.molding.quality_pct)}`} tone={data.oee.molding.oee_pct < 85 ? "warn" : "good"} />
            <Kpi label="OEE plant" value={pct(data.oee.plant.oee_pct)} />
            <Kpi label="Worst machine" value={data.oee.worst_machine} sub={`OEE ${pct(data.oee.worst_machine_oee_pct)}`} tone="bad" />
            <Kpi label="Top-3 losses yesterday" value={formatIdrCompact(data.top_losses.reduce((a, l) => a + l.loss_idr, 0))} />
          </div>
          <div className="grid2">
            <Card title="Three biggest losses" actions={<span className="muted small">{formatIdrCompact(lossTotal)} total</span>}>
              <ol className="loss-list">
                {data.top_losses.map((l, i) => (
                  <li key={l.id}>
                    <Link to={`/losses/${encodeURIComponent(l.id)}`} className="loss-row" title="See how this loss is calculated">
                      <span className="loss-rank">{i + 1}</span>
                      <div className="loss-main">
                        <div className="loss-head">
                          <strong>{l.title}</strong>
                          <span className="loss-idr" title={formatIdr(l.loss_idr)}>{formatIdrCompact(l.loss_idr)}</span>
                        </div>
                        <div className="muted small">{l.detail}</div>
                        <div className="loss-bar" aria-label={`${Math.round((100 * l.loss_idr) / lossTotal)}% of the three`}>
                          <span style={{ width: `${(100 * l.loss_idr) / lossTotal}%` }} />
                        </div>
                      </div>
                      <span className="risk-go" aria-hidden="true">›</span>
                    </Link>
                  </li>
                ))}
              </ol>
            </Card>
            <Card title="Risks this week" actions={<span className="muted small">{["high", "medium"].map((s) => `${risks.filter((r) => r.severity === s).length} ${s}`).join(" · ")}</span>}>
              <ul className="risk-list">
                {risks.map((r) => (
                  <li key={r.title}>
                    <Link to={MODULE_PATH[r.module] ?? "/"} className={`risk-row ${r.severity}`}>
                      <div className="risk-main">
                        <span className="risk-module">{r.module}</span>
                        <strong>{r.title}</strong>
                        <span className="muted small">{r.detail}</span>
                      </div>
                      <span className="risk-go" aria-hidden="true">›</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </Card>
          </div>
          <Sustainability />
          <Card title="The disruption">
            <p>
              OEM-A raises its <strong>FB-ARKA</strong> front-bumper order by 40%, the PP-EPDM-T20 resin vendor is late, and injection molding machine
              <strong> IMM-04</strong> is degrading. The Orchestrator traces this across all seven modules and proposes one plan; nothing changes until you approve it.
            </p>
          </Card>
          <Source>deterministic tools over BigQuery tables fv.fact_production, fv.fact_downtime, fv.fact_customer_order (no Gemini)</Source>
        </>
      )}
    </>
  );
}
