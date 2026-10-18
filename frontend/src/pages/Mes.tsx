import { AskAgent } from "../components/agents";
import { Bars } from "../components/charts";
import { Badge, Card, Kpi, Source, Status, Table, pct } from "../components/ui";
import { formatIdr } from "../lib/format";
import { useData } from "../lib/store";
import ShopFloor from "../twin/ShopFloor";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

export default function MesPage() {
  const { data, error, loading } = useData<Any>("/api/modules/mes/summary");
  const h = data?.health;
  return (
    <>
      <h1>MES / Shop Floor</h1>
      <ShopFloor />
      <Status loading={loading} error={error} />
      {data && (
        <>
          <div className="kpis">
            <Kpi label="OEE molding (yesterday)" value={pct(data.oee_yesterday.plant_molding.oee_pct)} sub={`7 days: ${pct(data.oee_7d.plant_molding.oee_pct)}`} />
            <Kpi label="IMM-04 breakdown risk" value={h.breakdown_risk} sub={h.after_maintenance ? "maintenance approved" : `alarm in ${h.days_to_alarm} days (${h.predicted_breakdown_date})`} tone={h.breakdown_risk === "high" ? "bad" : h.breakdown_risk === "medium" ? "warn" : "good"} />
            <Kpi label="IMM-04 cycle" value={`${h.current_cycle_s} s`} sub={`ideal ${h.ideal_cycle_s} s, +${h.cycle_trend_s_per_day} s/day`} tone="warn" />
            <Kpi label="IMM-04 MTBF" value={`${h.mtbf_hours_last_2_weeks} h`} sub={`was ${h.mtbf_hours_first_6_weeks} h`} tone="warn" />
          </div>
          <div className="grid2">
            <Card title="OEE per machine (yesterday)">
              <Bars items={Object.entries(data.oee_yesterday.by_machine).map(([m, v]: [string, Any]) => ({ label: m, value: v.oee_pct, tone: v.oee_pct < 80 ? "bad" : v.oee_pct < 85 ? "warn" : "good" }))} unit="%" max={100} threshold={85} />
              <Source>{data.oee_yesterday.source} · {data.oee_yesterday.formula}</Source>
            </Card>
            <Card title="Downtime Pareto (30 days)">
              <Table
                cols={[{ key: "r", label: "Reason" }, { key: "h", label: "Hours", align: "right" }, { key: "l", label: "Loss", align: "right" }, { key: "c", label: "Cum.", align: "right" }]}
                rows={data.pareto.top.map((p: Any) => ({ r: `${p.reason_name} (${p.reason_category})`, h: p.hours, l: formatIdr(p.loss_idr), c: pct(p.cumulative_pct, 0) }))}
              />
              <Source>{data.pareto.source}</Source>
            </Card>
          </div>
          <Card title="IMM health (cycle-time trend and MTBF)">
            <Table
              cols={[{ key: "m", label: "Machine" }, { key: "c", label: "Cycle now / ideal", align: "right" }, { key: "t", label: "Trend", align: "right" }, { key: "d", label: "Days to alarm", align: "right" }, { key: "mt", label: "MTBF (2 wk)", align: "right" }, { key: "r", label: "Risk" }]}
              rows={data.fleet.map((f: Any) => ({ m: f.machine_id, c: `${f.current_cycle_s} / ${f.ideal_cycle_s} s`, t: `${f.cycle_trend_s_per_day} s/day`, d: f.days_to_alarm ?? "–", mt: f.mtbf_hours_last_2_weeks ? `${f.mtbf_hours_last_2_weeks} h` : "–", r: <Badge tone={f.breakdown_risk === "high" ? "bad" : f.breakdown_risk === "medium" ? "warn" : "good"}>{f.breakdown_risk}</Badge> }))}
            />
            <Source>{h.method}</Source>
          </Card>
          <AskAgent module="mes" placeholder="Is IMM-04 going to break down, and what do the cameras see?" />
        </>
      )}
    </>
  );
}
