import { Link } from "react-router-dom";
import { AskAgent } from "../components/agents";
import { Bars, TrendChart } from "../components/charts";
import { Icon } from "../components/icons";
import { Badge, Card, MetricCard, Source, Status, Table, type Tone, num, pct } from "../components/ui";
import { formatIdr, formatIdrCompact } from "../lib/format";
import { useData } from "../lib/store";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

/** Molding OEE target, the same line the per-machine bars are drawn against. */
const OEE_TARGET = 85;
const MODULE_PATH: Record<string, string> = { mes: "/mes", material: "/material", delivery: "/delivery", quality: "/quality", purchasing: "/purchasing" };

/** Band label: every section answers one management question, shown next to its title. */
function SectionHeading({ title, question }: { title: string; question: string }) {
  return (
    <div className="exec-band">
      <span className="exec-band-title">{title}</span>
      <span className="exec-band-question">{question}</span>
    </div>
  );
}

/** Day-on-day change; `higherIsBetter` decides whether a rise reads as good news. */
function change(series: number[], higherIsBetter: boolean, unit: string, digits = 1) {
  if (series.length < 2) return undefined;
  const d = series[series.length - 1] - series[series.length - 2];
  const mag = Math.abs(d).toLocaleString("en-US", { maximumFractionDigits: digits });
  const sign = d > 0 ? "▲" : d < 0 ? "▼" : "■";
  return { text: `${sign} ${mag}${unit} vs day before`, good: d === 0 ? null : d > 0 === higherIsBetter };
}

/** Factory Overview: the executive dashboard. Hero, KPI, production and OEE, machines, losses, schedule and exceptions. */
export default function FactoryOverviewPage() {
  const { data, error, loading } = useData<Any>("/api/modules/mes/summary");
  const { data: quality } = useData<Any>("/api/modules/quality/summary");
  const { data: delivery } = useData<Any>("/api/modules/delivery/summary");
  const { data: orders } = useData<Any>("/api/modules/orders/summary");
  const { data: briefing } = useData<Any>("/api/briefing");

  const h = data?.health;
  const daily: Any[] = data?.trend?.daily ?? [];
  const s = (k: string) => daily.map((d) => d[k] as number);
  const y = data?.oee_yesterday.plant_molding;
  const risk = delivery?.risk;
  const book = orders?.book;
  const atRiskMachines = new Set((data?.fleet ?? []).filter((f: Any) => f.breakdown_risk !== "low").map((f: Any) => f.machine_id));
  const imm = (data?.fleet ?? []).length;
  const tone = (v: number, warn: number, bad: number, higherIsBetter = true): Tone =>
    higherIsBetter ? (v < bad ? "bad" : v < warn ? "warn" : "good") : v > bad ? "bad" : v > warn ? "warn" : "good";

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Factory Overview</h1>
          <p className="muted">Executive view of the Vidad bumper plant · production day {data?.oee_yesterday.window[1] ?? "…"}</p>
        </div>
      </div>
      <Status loading={loading} error={error} />
      {data && y && (
        <>
          <section className="exec-hero">
            <div className="exec-hero-top">
              <div>
                <div className="exec-hero-plant"><span className="pulse" />Vidad bumper plant · Molding</div>
                <div className="exec-hero-shift">
                  3 shifts · production day {data.oee_yesterday.window[1]} · <strong>{imm - atRiskMachines.size} of {imm} injection machines healthy</strong>
                </div>
              </div>
              <span className="exec-hero-pill"><Icon name="gauge" size={15} />OEE {pct(y.oee_pct)}</span>
            </div>
            <div className="exec-hero-figure">
              <div className="exec-hero-caption">Good bumpers molded on the last production day</div>
              <div className="exec-hero-row">
                <span className="exec-hero-number">{num(y.output_quantity)} pcs</span>
                <span className="exec-hero-of">/ {num(y.total_quantity)} shot</span>
                <span className={`exec-hero-badge ${y.quality_pct >= 98 ? "good" : "warn"}`}>{pct(y.quality_pct)} first-pass quality</span>
              </div>
            </div>
            <div className="exec-hero-actions">
              <Link className="hero-btn primary" to="/mes"><Icon name="factory" />Open MES shop floor</Link>
              <Link className="hero-btn" to="/quality"><Icon name="inspect" />Quality inspection</Link>
              <Link className="hero-btn" to="/actions"><Icon name="inbox" />Action Center</Link>
            </div>
          </section>

          <SectionHeading title="Executive KPI" question="How is the plant doing right now?" />
          <div className="metric-grid">
            <MetricCard label="OEE molding" icon="gauge" value={pct(y.oee_pct)} tone={tone(y.oee_pct, OEE_TARGET, 80)} delta={change(s("oee_pct"), true, " pt")} sub={`Target ${OEE_TARGET}% · 7 d ${pct(data.oee_7d.plant_molding.oee_pct)}`} spark={s("oee_pct")} />
            <MetricCard label="Availability" icon="clock" value={pct(y.availability_pct)} tone={tone(y.availability_pct, 90, 85)} delta={change(s("availability_pct"), true, " pt")} spark={s("availability_pct")} />
            <MetricCard label="Performance" icon="bolt" value={pct(y.performance_pct)} tone={tone(y.performance_pct, 92, 88)} delta={change(s("performance_pct"), true, " pt")} spark={s("performance_pct")} />
            <MetricCard label="Quality" icon="check" value={pct(y.quality_pct)} tone={tone(y.quality_pct, 98, 96)} delta={change(s("quality_pct"), true, " pt")} spark={s("quality_pct")} />
            <MetricCard label="Good output" icon="box" value={`${num(y.output_quantity)} pcs`} tone="accent" delta={change(s("output_quantity"), true, " pcs", 0)} spark={s("output_quantity")} />
            <MetricCard label="Downtime" icon="timer" value={`${num(y.downtime_minutes)} min`} tone={tone(y.downtime_minutes, 120, 240, false)} delta={change(s("downtime_minutes"), false, " min", 0)} spark={s("downtime_minutes")} />
            <MetricCard
              label="Orders at risk"
              icon="alert"
              value={risk ? risk.count : "…"}
              tone={risk ? (risk.count ? "bad" : "good") : "accent"}
              sub={risk ? (risk.count ? `${formatIdrCompact(risk.total_penalty_idr)} penalty exposure` : "every open order covered") : undefined}
            />
            <MetricCard
              label="On-time delivery (30 d)"
              icon="truck"
              value={book ? pct(book.on_time_delivery_pct) : "…"}
              tone={book?.on_time_delivery_pct != null ? tone(book.on_time_delivery_pct, 95, 90) : "accent"}
              sub={book ? `${book.shipments_30d} shipments · ${book.open_count} orders open` : undefined}
            />
          </div>

          <SectionHeading title="Production & OEE performance" question="Is molding efficient, and where is it slipping?" />
          <div className="grid2">
            <Card title={`Molding OEE trend (${daily.length} production days)`}>
              <TrendChart
                label="Daily molding OEE, availability, performance and quality"
                dates={s("date") as unknown as string[]}
                series={[
                  { name: "OEE", values: s("oee_pct"), tone: "accent" },
                  { name: "Availability", values: s("availability_pct"), tone: "info" },
                  { name: "Performance", values: s("performance_pct"), tone: "warn" },
                  { name: "Quality", values: s("quality_pct"), tone: "good" },
                ]}
                target={OEE_TARGET}
              />
              <Source>{data.trend?.source ?? data.oee_yesterday.source} · {data.oee_yesterday.formula}</Source>
            </Card>
            <Card title="OEE per machine (last production day)">
              <Bars items={Object.entries(data.oee_yesterday.by_machine).map(([m, v]: [string, Any]) => ({ label: m, value: v.oee_pct, tone: v.oee_pct < 80 ? "bad" : v.oee_pct < OEE_TARGET ? "warn" : "good" }))} unit="%" max={100} threshold={OEE_TARGET} />
              <Source>{data.oee_yesterday.source}. The line marks the {OEE_TARGET}% target.</Source>
            </Card>
          </div>

          <SectionHeading title="Machine performance" question="Which machine loses the most, and which one will break next?" />
          <Card
            title="Injection machine health (cycle-time trend and MTBF)"
            actions={h.after_maintenance ? <Badge tone="good">IMM-04 maintenance approved</Badge> : <Badge tone={h.breakdown_risk === "high" ? "bad" : "warn"}>IMM-04 alarm in {h.days_to_alarm} days ({h.predicted_breakdown_date})</Badge>}
          >
            <Table
              cols={[{ key: "m", label: "Machine" }, { key: "o", label: "OEE", align: "right" }, { key: "a", label: "A", align: "right" }, { key: "p", label: "P", align: "right" }, { key: "q", label: "Q", align: "right" }, { key: "w", label: "Biggest loss" }, { key: "c", label: "Cycle now / ideal", align: "right" }, { key: "t", label: "Trend", align: "right" }, { key: "mt", label: "MTBF (2 wk)", align: "right" }, { key: "r", label: "Risk" }]}
              rows={data.fleet.map((f: Any) => {
                const v = data.oee_yesterday.by_machine[f.machine_id];
                return {
                  m: <strong>{f.machine_id}</strong>,
                  o: v ? pct(v.oee_pct) : "–",
                  a: v ? pct(v.availability_pct) : "–",
                  p: v ? pct(v.performance_pct) : "–",
                  q: v ? pct(v.quality_pct) : "–",
                  w: v ? `${v.losses[0].factor.toLowerCase()} (${pct(v.losses[0].loss_pct)} lost)` : "–",
                  c: `${f.current_cycle_s} / ${f.ideal_cycle_s} s`,
                  t: `${f.cycle_trend_s_per_day} s/day`,
                  mt: f.mtbf_hours_last_2_weeks ? `${f.mtbf_hours_last_2_weeks} h` : "–",
                  r: <Badge tone={f.breakdown_risk === "high" ? "bad" : f.breakdown_risk === "medium" ? "warn" : "good"}>{f.breakdown_risk}</Badge>,
                };
              })}
            />
            <Source>{h.method}</Source>
          </Card>

          <SectionHeading title="Loss analysis" question="What drives production loss and quality loss?" />
          <div className="grid2">
            <Card title="Downtime Pareto (30 days)" actions={<span className="muted small">{formatIdrCompact(data.pareto.total_loss_idr)} · {data.pareto.total_hours} h</span>}>
              <Bars items={data.pareto.top.map((p: Any) => ({ label: p.reason_name, value: p.hours, tone: p.cumulative_pct <= 80 ? "bad" : "warn" }))} unit=" h" />
              <Table
                cols={[{ key: "c", label: "Loss category" }, { key: "h", label: "Hours", align: "right" }, { key: "l", label: "Loss", align: "right" }, { key: "n", label: "Events", align: "right" }]}
                rows={data.pareto.by_loss_category.map((c: Any) => ({ c: c.loss_category, h: c.hours, l: formatIdr(c.loss_idr), n: c.events }))}
              />
              <Source>{data.pareto.source}. Red bars are the causes that make up the first 80% of the loss.</Source>
            </Card>
            <Card title={`Defect Pareto (${quality?.correlation.window_days ?? 45} days)`} actions={quality && <span className="muted small">{pct(quality.correlation.defect_rate_pct, 2)} defect rate · {num(quality.correlation.inspected_qty)} inspected</span>}>
              {quality ? (
                <>
                  <Bars items={quality.correlation.defect_pareto.slice(0, 6).map((d: Any) => ({ label: d.defect_name, value: d.qty, tone: "bad" as const }))} unit=" pcs" />
                  {quality.correlation.suspect_lot && (
                    <p className="exec-note">
                      <Badge tone="warn">suspect lot</Badge> Resin lot <strong>{quality.correlation.suspect_lot}</strong> runs{" "}
                      {quality.correlation.lot_findings[0]?.lift}× the normal defect rate. <Link to="/quality">Review hold</Link>
                    </p>
                  )}
                  <Source>{quality.correlation.source}</Source>
                </>
              ) : (
                <p className="muted">Loading…</p>
              )}
            </Card>
          </div>

          <SectionHeading title="Schedule & exceptions" question="Is the delivery schedule safe, and what needs attention now?" />
          <div className="grid2">
            <Card title="Orders at risk of late delivery" actions={<Link to="/delivery" className="small">Open Delivery</Link>}>
              {risk ? (
                <Table
                  empty="No open order is at risk."
                  cols={[{ key: "o", label: "Order" }, { key: "c", label: "Customer" }, { key: "s", label: "SKU" }, { key: "d", label: "Due", align: "right" }, { key: "l", label: "Late", align: "right" }, { key: "p", label: "Penalty", align: "right" }]}
                  rows={risk.orders_at_risk.map((r: Any) => ({ o: r.order_id, c: r.customer_name, s: r.sku, d: r.requested_delivery_date, l: `${r.days_late} d`, p: formatIdrCompact(r.penalty_idr) }))}
                />
              ) : (
                <p className="muted">Loading…</p>
              )}
              {risk && <Source>{risk.source}</Source>}
            </Card>
            <Card title="Operational alerts this week">
              {briefing ? (
                <ul className="exec-alerts">
                  {briefing.risks.map((r: Any) => (
                    <li key={r.title} className={r.severity}>
                      <Badge tone={r.severity === "high" ? "bad" : "warn"}>{r.severity}</Badge>
                      <div>
                        <Link to={MODULE_PATH[r.module] ?? "/"}>{r.title}</Link>
                        <div className="muted small">{r.detail}</div>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">Loading…</p>
              )}
            </Card>
          </div>

          <AskAgent module="mes" placeholder="Is IMM-04 going to break down, and what do the cameras see?" />
        </>
      )}
    </>
  );
}
