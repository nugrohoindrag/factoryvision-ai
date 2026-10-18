import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AppProvider } from "../lib/store";
import PlanWizardPage from "./PlanWizard";
import ProductionPlansPage from "./ProductionPlans";

const STEPS = ["Demand", "Production Plan", "Work Order", "Scheduling", "Resource", "Confirmation"];
const steps = (reachable: number, blocked = "Add at least one demand in step 1 before setting planned quantities.") =>
  STEPS.map((label, i) => ({ step: i + 1, label, reachable: i < reachable, blocked_by: i < reachable ? null : blocked }));

const CANDIDATE = {
  order_id: "SO-1",
  order_number: "CO-270204-OEMA",
  customer_id: "CUS-OEMA",
  customer_name: "Nusantara Motor (OEM-A)",
  sku: "FB-ARKA",
  product_name: "Front bumper ARKA (MPV)",
  ordered_qty: 735,
  planned_qty: 0,
  outstanding_qty: 735,
  requested_delivery_date: "2027-02-04",
  in_period: true,
  at_risk: true,
};
const plan = (over = {}) => ({
  plan_id: "plan-1",
  plan_number: "PP-2027-02-002",
  baseline: false,
  period_start: "2027-02-01",
  period_end: "2027-02-07",
  status: "Draft",
  capacity_status: null,
  planning_utilization_pct: 80,
  forecast_id: null,
  capacity_plan_id: null,
  created_by: "demo-manager",
  editable: true,
  locked_reason: null,
  lines: [],
  work_orders: [],
  schedule_run: null,
  readiness: { steps: steps(1), resume_step: 1, capacity_up_required_lines: [], confirmed_work_orders: 0 },
  summary: { line_count: 0, total_planned_qty: 0, total_demand_qty: 0, work_order_count: 0, customer_orders: [] },
  ...over,
});
const LINE = {
  line_id: "ppl-1",
  sku: "FB-ARKA",
  product_name: "Front bumper ARKA (MPV)",
  demand_qty: 735,
  forecast_qty: 0,
  planned_qty: 735,
  priority: 1,
  required_delivery_date: "2027-02-04",
  capacity_status: "Additional Demand",
  capacity: null,
  sources: [{ demand_id: "ppd-1", order_id: "SO-1", order_number: "CO-270204-OEMA", customer_name: "Nusantara Motor (OEM-A)", sku: "FB-ARKA", demand_qty: 735, requested_delivery_date: "2027-02-04" }],
};
const DRAFT = { plan: plan(), capacity: null, candidates: [CANDIDATE], notes: [] };
const PLANNING = { plan: plan({ status: "Planning", capacity_status: "Additional Demand", lines: [LINE], readiness: { steps: steps(3, "Generate work orders in step 3 before scheduling."), resume_step: 1, capacity_up_required_lines: [], confirmed_work_orders: 0 } }), capacity: null, candidates: [], notes: [] };

const calls: { url: string; method: string; body?: unknown }[] = [];

function mockApi(detail: unknown = DRAFT) {
  calls.length = 0;
  globalThis.fetch = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
    let body: unknown = { pending: 0, actions: [] };
    if (url.endsWith("/demands") && method === "POST") body = PLANNING;
    else if (url.startsWith("/api/production-plans/plan-1")) body = detail;
    else if (url.startsWith("/api/production-plans"))
      body = {
        as_of: "2027-02-01",
        total: 2,
        statuses: ["Draft", "Planning", "Ready", "Confirmed", "In Execution", "Completed", "Cancelled"],
        default_utilization_pct: 80,
        plans: [
          { plan_id: "plan-1", plan_number: "PP-2027-02-002", baseline: false, period_start: "2027-02-01", period_end: "2027-02-07", status: "Planning", capacity_status: "Capacity Up Required", line_count: 1, total_planned_qty: 735, created_by: "demo-manager" },
          { plan_id: "PP-2027-02", plan_number: "PP-2027-02", baseline: true, period_start: "2027-02-01", period_end: "2027-02-28", status: "In Execution", capacity_status: "Capacity Up Required", line_count: 6, total_planned_qty: 96120, created_by: "OEM monthly schedule" },
        ],
      };
    else if (url.includes("/api/mode")) body = { ai_enabled: false };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppProvider>
        <Routes>
          <Route path="/production-plans" element={<ProductionPlansPage />} />
          <Route path="/production-plans/:planId" element={<PlanWizardPage />} />
        </Routes>
      </AppProvider>
    </MemoryRouter>,
  );
}

describe("Production plans", () => {
  it("lists session and monthly plans with status and worst capacity", async () => {
    mockApi();
    renderAt("/production-plans");
    expect(await screen.findByText("PP-2027-02-002")).toBeInTheDocument();
    expect(screen.getByText("This session")).toBeInTheDocument();
    expect(screen.getAllByText("Capacity Up Required").length).toBeGreaterThan(0);
    expect(screen.getByText("Open wizard")).toHaveAttribute("href", "/production-plans/plan-1");
    expect(screen.getByText("View")).toHaveAttribute("href", "/production-plans/PP-2027-02");
  });

  it("locks the later steps and says what is missing", async () => {
    mockApi();
    renderAt("/production-plans/plan-1");
    expect(await screen.findByText("Step 1 — Demand")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Next/ })).toBeDisabled();
    expect(screen.getByText("Add at least one demand in step 1 before setting planned quantities.")).toBeInTheDocument();
  });

  it("adds an open order line as demand and unlocks step 2", async () => {
    mockApi();
    renderAt("/production-plans/plan-1");
    await screen.findByText("CO-270204-OEMA");
    fireEvent.change(screen.getByLabelText("Quantity for CO-270204-OEMA FB-ARKA"), { target: { value: "500" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/demands"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/demands"))?.body).toEqual({ order_id: "SO-1", demand_qty: 500 });
    await waitFor(() => expect(screen.getByRole("button", { name: /Next/ })).toBeEnabled());
  });

  it("shows the server's capacity status on step 2 and saves planned and priority", async () => {
    mockApi({ ...PLANNING, plan: { ...PLANNING.plan, readiness: { ...PLANNING.plan.readiness, resume_step: 2 } } });
    renderAt("/production-plans/plan-1");
    expect(await screen.findByText("Step 2 — Production Plan")).toBeInTheDocument();
    expect(screen.getAllByText("Additional Demand").length).toBeGreaterThan(0);
    fireEvent.change(screen.getByLabelText("Planned FB-ARKA"), { target: { value: "600" } });
    expect(screen.getByText("135 below demand")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && c.url.endsWith("/lines/ppl-1"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/lines/ppl-1"))?.body).toEqual({ planned_qty: 600, priority: 1 });
  });

  it("shows a confirmed plan read-only", async () => {
    const locked = plan({ status: "In Execution", editable: false, locked_reason: "This plan is In Execution; a confirmed plan is a production commitment and does not change.", lines: [LINE], readiness: { steps: steps(6), resume_step: 6, capacity_up_required_lines: [], confirmed_work_orders: 3 }, summary: { line_count: 1, total_planned_qty: 735, total_demand_qty: 735, work_order_count: 3, customer_orders: ["CO-270204-OEMA"] }, confirmed_by: "demo-manager", confirmed_at: "2027-02-01T08:00:00+07:00" });
    mockApi({ plan: locked, capacity: null, candidates: [], notes: [] });
    renderAt("/production-plans/plan-1");
    expect(await screen.findByText("Step 6 — Confirmation")).toBeInTheDocument();
    expect(screen.getByText(/does not change/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm production plan" })).not.toBeInTheDocument();
  });
});
