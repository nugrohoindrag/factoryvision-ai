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

const SUMMARY = {
  module: "mes",
  oee_yesterday: { plant_molding: { oee_pct: 83.8 }, by_machine: { "IMM-04": machine }, source: "fv.fact_production", formula: "A = run/planned" },
  oee_7d: { plant_molding: { oee_pct: 82.9 } },
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
    by_loss_category: [{ loss_category: "Utility", hours: 48.4, loss_idr: 348080000, events: 49 }],
    source: "fv.fact_downtime x fv.dim_downtime_reason",
  },
  fleet: [{ machine_id: "IMM-04", current_cycle_s: 79.28, ideal_cycle_s: 68, cycle_trend_s_per_day: 0.336, days_to_alarm: 6.9, breakdown_risk: "high", mtbf_hours_last_2_weeks: 21.5 }],
};

beforeEach(() => {
  globalThis.fetch = vi.fn().mockImplementation(async (url: string) => {
    const body = url.includes("/api/modules/mes/summary") ? SUMMARY : { ai_enabled: false };
    return { ok: true, json: async () => body };
  });
});

it("shows OEE, the downtime Pareto and injection-machine health on one overview", async () => {
  render(
    <MemoryRouter>
      <AppProvider>
        <FactoryOverviewPage />
      </AppProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByRole("heading", { name: "Factory Overview" })).toBeInTheDocument();
  expect(await screen.findByText("OEE per machine (yesterday)")).toBeInTheDocument();
  expect(screen.getByText("Downtime Pareto (30 days)")).toBeInTheDocument();
  expect(screen.getByText("IMM health (cycle-time trend and MTBF)")).toBeInTheDocument();
  expect(screen.getByText("OEE breakdown (yesterday): Availability × Performance × Quality")).toBeInTheDocument();
  expect(screen.getByText("performance (14.4% lost)")).toBeInTheDocument();
});
