import { Link } from "react-router-dom";
import { Badge, Card, Kpi, Source, Status, pct } from "../components/ui";
import { formatIdr, formatIdrCompact } from "../lib/format";
import { useApp, useData } from "../lib/store";

type Briefing = {
  as_of: string;
  yesterday: string;
  oee: { molding: { oee_pct: number; availability_pct: number; performance_pct: number; quality_pct: number }; plant: { oee_pct: number }; worst_machine: string; worst_machine_oee_pct: number };
  top_losses: { title: string; loss_idr: number; detail: string; kind: string }[];
  risks: { module: string; severity: string; title: string; detail: string }[];
};

const MODULE_PATH: Record<string, string> = { mes: "/mes", material: "/material", delivery: "/delivery", quality: "/quality" };

export default function BriefingPage() {
  const { data, error, loading } = useData<Briefing>("/api/briefing");
  const { startDisruption, run, live } = useApp();
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Plant Briefing</h1>
          <p className="muted">Vidad bumper plant · Monday {data?.as_of ?? "…"} 07:00 WIB · numbers for {data?.yesterday ?? "yesterday"}</p>
        </div>
        <button className="primary big" onClick={startDisruption} disabled={run.status === "running"}>
          {run.status === "running" ? "Agents working…" : `Run Disruption Response (${live ? "Live" : "Replay"})`}
        </button>
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
            <Card title="Three biggest losses (Rupiah)">
              <ol className="losses">
                {data.top_losses.map((l) => (
                  <li key={l.title}>
                    <strong>{l.title}</strong> <span className="neg">{formatIdr(l.loss_idr)}</span>
                    <div className="muted small">{l.detail}</div>
                  </li>
                ))}
              </ol>
            </Card>
            <Card title="Risks this week">
              <ul className="risks">
                {data.risks.map((r) => (
                  <li key={r.title}>
                    <Badge tone={r.severity === "high" ? "bad" : "warn"}>{r.severity}</Badge> <Link to={MODULE_PATH[r.module] ?? "/"}>{r.title}</Link>
                    <div className="muted small">{r.detail}</div>
                  </li>
                ))}
              </ul>
            </Card>
          </div>
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
