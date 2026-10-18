import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AppProvider } from "../lib/store";
import FactoryOverviewPage from "./FactoryOverview";

const LOSSES = [
  { factor: "PERFORMANCE", loss_pct: 14.4 },
  { factor: "AVAILABILITY", loss_pct: 6.2 },
  { factor: "QUALITY", loss_pct: 0.4 },
];
const machine = { availability_pct: 93.8, performance_pct: 85.6, quality_pct: 96.2, oee_pct: 77.2, losses: LOSSES, unexplained_minutes: 84 };
const day = (date: string, oee: number) => ({ date, oee_pct: oee, availability_pct: 92, performance_pct: 92, quality_pct: 98, output_quantity: 4900, total_quantity: 5000, downtime_minutes: 60 });

const SUMMARY = {
  module: "mes",
  oee_yesterday: {
    window: ["2027-01-30", "2027-01-30"],
    plant_molding: { oee_pct: 83.8, availability_pct: 92.7, performance_pct: 92.3, quality_pct: 98, output_quantity: 4983, total_quantity: 5087, downtime_minutes: 55 },
    by_machine: { "IMM-04": machine },
    source: "fv.fact_production",
    formula: "A = run/planned",
  },
  oee_7d: { plant_molding: { oee_pct: 82.9 } },
  trend: { daily: [day("2027-01-29", 81.6), day("2027-01-30", 83.8)], source: "fv.fact_production (IMM-01..06)" },
  health: {
    breakdown_risk: "high",
    days_to_alarm: 6.9,
    predicted_breakdown_date: "2027-02-08",
    current_cycle_s: 79.28,
    ideal_cycle_s: 68,
    cycle_trend_s_per_day: 0.336,
    mtbf_hours_last_2_weeks: 21.5,
    mtbf_hours_first_6_weeks: 87.6,
    method: "linear fit of daily cycle time",
    after_maintenance: false,
  },
  pareto: {
    top: [{ reason_name: "Heater band failure", hours: 12.7, cumulative_pct: 36 }],
    total_hours: 82.4,
    total_loss_idr: 492000000,
    by_loss_category: [{ loss_category: "Utility", hours: 48.4, loss_idr: 348080000, events: 49 }],
    source: "fv.fact_downtime x fv.dim_downtime_reason",
  },
  fleet: [{ machine_id: "IMM-04", current_cycle_s: 79.28, ideal_cycle_s: 68, cycle_trend_s_per_day: 0.336, days_to_alarm: 6.9, breakdown_risk: "high", mtbf_hours_last_2_weeks: 21.5 }],
};

const QUALITY = {
  correlation: {
    window_days: 45,
    inspected_qty: 120000,
    defect_rate_pct: 1.9,
    suspect_lot: "RL-2701",
    lot_findings: [{ resin_lot_id: "RL-2701", lift: 3.1 }],
    defect_pareto: [{ defect_code: "SS", defect_name: "Short shot", qty: 410 }],
    source: "fv.fact_inspection",
  },
};
const DELIVERY = {
  risk: {
    count: 1,
    total_penalty_idr: 190000000,
    orders_at_risk: [{ order_id: "SO-270204-002", customer_name: "Arka Motor", sku: "FB-ARKA", requested_delivery_date: "2027-02-04", days_late: 3, penalty_idr: 90000000 }],
    source: "fv.fact_customer_order",
  },
};
const ORDERS = { book: { on_time_delivery_pct: 96.4, shipments_30d: 55, open_count: 12 } };
const BRIEFING = { risks: [{ module: "material", severity: "high", title: "Resin PP-EPDM-T20 runs out in 9 days", detail: "138,400 kg on hand" }] };
const BODIES: [string, unknown][] = [
  ["/api/modules/mes/summary", SUMMARY],
  ["/api/modules/quality/summary", QUALITY],
  ["/api/modules/delivery/summary", DELIVERY],
  ["/api/modules/orders/summary", ORDERS],
  ["/api/briefing", BRIEFING],
];

beforeEach(() => {
  globalThis.fetch = vi.fn().mockImplementation(async (url: string) => {
    const body = BODIES.find(([p]) => url.includes(p))?.[1] ?? { ai_enabled: false };
    return { ok: true, json: async () => body };
  });
});

it("lays the overview out as an executive dashboard: hero, KPI, OEE, machines, losses and exceptions", async () => {
  render(
    <MemoryRouter>
      <AppProvider>
        <FactoryOverviewPage />
      </AppProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByRole("heading", { name: "Factory Overview" })).toBeInTheDocument();
  expect(await screen.findAllByText("4,983 pcs")).toHaveLength(2);
  for (const band of ["Executive KPI", "Production & OEE performance", "Machine performance", "Loss analysis", "Schedule & exceptions"]) {
    expect(screen.getByText(band)).toBeInTheDocument();
  }
  expect(screen.getByText("OEE per machine (last production day)")).toBeInTheDocument();
  expect(screen.getByText("Downtime Pareto (30 days)")).toBeInTheDocument();
  expect(screen.getByText("performance (14.4% lost)")).toBeInTheDocument();
  expect(screen.getByText("▲ 2.2 pt vs day before")).toBeInTheDocument();
  expect(await screen.findByText("SO-270204-002")).toBeInTheDocument();
  expect(await screen.findByText("Short shot")).toBeInTheDocument();
  expect(await screen.findByText("Resin PP-EPDM-T20 runs out in 9 days")).toBeInTheDocument();
});
