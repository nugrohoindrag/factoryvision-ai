import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AppProvider } from "../lib/store";
import CapacityPage, { normalizeRow } from "./Capacity";
import { PlanningPage } from "./Planning";

const SUMMARY = {
  current: {
    month: "2027-02-01",
    bottleneck_machine: "IMM-04",
    bottleneck_load_pct: 112,
    groups: [{ capacity_gap_pct: 12, status: "Capacity Up Required" }],
    demand_change_pct: {},
    working_days: 24,
    machines: [{ machine_id: "IMM-04", skus: ["FB-ARKA"], load_pct: 112 }],
    source: "fv.fact_monthly_plan",
  },
};

const row = (extra = {}) => ({
  plan_id: "p1",
  plan_number: "PP-2027-02",
  period_start: "2027-02-01",
  period_end: "2027-02-28",
  status: "Draft",
  utilization_pct: 80,
  forecast_id: "FC-2027-02-001",
  created_at: "2027-01-20T08:00:00Z",
  created_by: "You",
  wizard_step: 1,
  origin: "user",
  capacity_status: "Capacity Up Required",
  line_count: 3,
  total_planned: 24880,
  age_days: 12,
  ageing: true,
  ...extra,
});
const PLANS = {
  plans: [
    row(),
    row({ plan_id: "p2", plan_number: "PP-2027-02-002", created_at: "2027-01-30T08:00:00Z", origin: "agent", ageing: false, age_days: 2, capacity_status: "Within Plan", line_count: 1, total_planned: 900 }),
  ],
};
const ACTIONS = {
  pending: 1,
  actions: [{ action_id: "a1", kind: "replan", status: "pending", title: "Re-plan February for FB-ARKA +40%", module: "planning", impact: {}, evidence: [] }],
};
const CAPACITY = {
  rows: [
    { sku: "FB-ARKA", product_name: "Front bumper ARKA", demand: 26746, total: 23880, planning: 19104, buffer: 4776, utilization_pct: 112, gap: 2866, status: "Capacity Up Required", uncomputed: [{ machine_id: "IMM-06", reason: "no cycle time for FB-ARKA" }] },
    { sku: "RB-ARKA", demand_qty: 9000, capacity: { total: 12000, planning: 9600, buffer: 2400, utilization_pct: 75, gap: 0, status: "Within Plan", uncomputed: [] } },
  ],
  kpis: { total: 35880, planning: 28704, buffer: 7176, utilization_pct: 99.6, gap: 2866 },
  formulas: { total: "Σ (available minutes ÷ cycle time) over compatible IMMs" },
};

const calls: { url: string; method: string; body?: unknown }[] = [];
function mockApi(over: (url: string, method: string) => unknown = () => undefined) {
  calls.length = 0;
  globalThis.fetch = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
    const o = over(url, method);
    if (o instanceof Response || (o && typeof o === "object" && "ok" in (o as object))) return o;
    let body: unknown = o ?? { pending: 0, actions: [] };
    if (o === undefined) {
      if (url.startsWith("/api/modules/planning/summary")) body = SUMMARY;
      else if (url.startsWith("/api/mode")) body = { ai_enabled: false };
      else if (url.startsWith("/api/actions")) body = ACTIONS;
      else if (url.startsWith("/api/plans") && method === "POST") body = { ...row({ plan_id: "new1", plan_number: "PP-2027-03" }), lines: [], demand: [], work_orders: [] };
      else if (url === "/api/plans/p1") body = { ...row(), demand: [{ order_number: "CO-270201-OEMA" }, { order_number: "CO-270205-TIER1" }, { order_number: "CO-270201-OEMA" }] };
      else if (url === "/api/plans/p2") body = { ...row(), demand: [] };
      else if (url.startsWith("/api/plans")) body = PLANS;
      else if (url.startsWith("/api/capacity/snapshots")) body = { snapshots: [{ snapshot_id: "CAP-2027-02-001", period_start: "2027-02-01", period_end: "2027-02-28", status: "Generated", kpis: { utilization_pct: 99.6, gap: 2866 }, created_at: "2027-02-01T07:00:00Z" }] };
      else if (url.startsWith("/api/capacity/recalculate")) body = { snapshot_id: "CAP-2027-02-002" };
      else if (url.startsWith("/api/capacity")) body = CAPACITY;
    }
    return { ok: true, json: async () => body };
  });
}

function renderAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <AppProvider>
        <Routes>
          <Route path="/planning" element={<PlanningPage />} />
          <Route path="/planning/:planId" element={<p>Wizard opened</p>} />
          <Route path="/capacity" element={<CapacityPage />} />
        </Routes>
      </AppProvider>
    </MemoryRouter>,
  );
}

it("lists plans newest first with capacity status, ageing draft, agent origin and forecast link", async () => {
  mockApi();
  renderAt("/planning");
  expect(await screen.findByText("Production plans (2)")).toBeInTheDocument();
  const rows = screen.getAllByRole("row").slice(1);
  expect(within(rows[0]).getByText("PP-2027-02-002")).toBeInTheDocument(); // newer created_at first
  expect(within(rows[0]).getByText("Agent")).toBeInTheDocument();
  expect(within(rows[0]).getByText("Within Plan")).toBeInTheDocument();
  expect(within(rows[1]).getByText("Capacity Up Required")).toBeInTheDocument();
  expect(within(rows[1]).getByLabelText("Ageing draft")).toHaveAttribute("title", "Draft for 12 days; this demand is not a commitment yet.");
  expect(within(rows[1]).getByText("24,880")).toBeInTheDocument();
  expect(await within(rows[1]).findByText("CO-270201-OEMA, CO-270205-TIER1")).toBeInTheDocument();
  expect(within(rows[1]).getByText("(3 lines)")).toBeInTheDocument();
  expect(within(rows[1]).getByRole("link", { name: "FC-2027-02-001" })).toHaveAttribute("href", "/forecast?snapshot=FC-2027-02-001");
  expect(screen.getByText(/1 re-plan proposal from the planning agent/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Review in the Action Center" })).toHaveAttribute("href", "/actions");
});

it("filters by status chip and period", async () => {
  mockApi();
  renderAt("/planning");
  await screen.findByText("Production plans (2)");
  fireEvent.click(within(screen.getByRole("group", { name: "Filter by status" })).getByRole("button", { name: "Confirmed" }));
  await waitFor(() => expect(calls.some((c) => c.url === "/api/plans?status=Confirmed")).toBe(true));
  fireEvent.change(screen.getByLabelText("Period from"), { target: { value: "2027-02-01" } });
  await waitFor(() => expect(calls.some((c) => c.url === "/api/plans?status=Confirmed&period_from=2027-02-01")).toBe(true));
});

it("shows the empty state with a create action, and an error with retry", async () => {
  mockApi((url) => (url.startsWith("/api/plans") ? { plans: [] } : undefined));
  renderAt("/planning");
  expect(await screen.findByText("No production plans yet")).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "+ New plan" })[1]);
  expect(screen.getByRole("dialog", { name: "New production plan" })).toBeInTheDocument();
});

it("shows a load error with a retry", async () => {
  mockApi((url) => (url.startsWith("/api/plans") ? { ok: false, statusText: "Bad", json: async () => ({ error: { code: "FV-PP-001", title: "Plan error", message: "boom" } }) } : undefined));
  renderAt("/planning");
  expect(await screen.findByText(/Could not load production plans: Plan error \(FV-PP-001\): boom/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
});

it("creates a plan for the plant month, rejects an end before the start, and opens the wizard", async () => {
  mockApi();
  renderAt("/planning");
  await screen.findByText("Production plans (2)");
  fireEvent.click(screen.getAllByRole("button", { name: "+ New plan" })[0]);
  const dialog = screen.getByRole("dialog", { name: "New production plan" });
  await waitFor(() => expect(within(dialog).getByLabelText("Period start")).toHaveValue("2027-02-01"));
  expect(within(dialog).getByLabelText("Period end")).toHaveValue("2027-02-28");
  fireEvent.change(within(dialog).getByLabelText("Period end"), { target: { value: "2027-01-15" } });
  expect(within(dialog).getByText("The period end cannot be before the start.")).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Create & open wizard" })).toBeDisabled();
  fireEvent.change(within(dialog).getByLabelText("Period start"), { target: { value: "2027-03-01" } });
  fireEvent.change(within(dialog).getByLabelText("Period end"), { target: { value: "2027-03-31" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Create & open wizard" }));
  expect(await screen.findByText("Wizard opened")).toBeInTheDocument();
  expect(calls.find((c) => c.url === "/api/plans" && c.method === "POST")?.body).toEqual({ period_start: "2027-03-01", period_end: "2027-03-31" });
});

it("shows the server's reason when creating fails", async () => {
  mockApi((url, method) =>
    url === "/api/plans" && method === "POST" ? { ok: false, statusText: "Bad", json: async () => ({ error: { code: "FV-PP-002", title: "Invalid period", message: "period_end is before period_start" } }) } : undefined,
  );
  renderAt("/planning");
  await screen.findByText("Production plans (2)");
  fireEvent.click(screen.getAllByRole("button", { name: "+ New plan" })[0]);
  fireEvent.click(screen.getByRole("button", { name: "Create & open wizard" }));
  expect(await screen.findByText(/Invalid period \(FV-PP-002\)/)).toBeInTheDocument();
});

it("capacity page: KPIs with formulas, per-SKU table, uncomputed machines, gap warning, recalculate and snapshots", async () => {
  mockApi();
  renderAt("/capacity");
  expect(await screen.findByText("35,880")).toBeInTheDocument();
  expect(screen.getByText("Σ (available minutes ÷ cycle time) over compatible IMMs")).toBeInTheDocument();
  expect(screen.getByText("Total − Planning")).toBeInTheDocument();
  expect(screen.getByText("max(Demand − Total, 0)")).toBeInTheDocument();
  expect(screen.getByText("Demand 35,746 ÷ Total 35,880")).toBeInTheDocument();
  expect(screen.getByText("Capacity not computed (1 machine)")).toBeInTheDocument();
  expect(screen.getByText(/no cycle time for FB-ARKA/)).toBeInTheDocument();
  expect(screen.getByText(/Capacity gap on FB-ARKA \(2,866 short\)/)).toBeInTheDocument();
  expect(screen.getByText("RB-ARKA")).toBeInTheDocument();
  expect(screen.getAllByText("CAP-2027-02-001")).toHaveLength(2); // latest-snapshot line and the snapshot table
  expect(calls.some((c) => c.url === "/api/capacity?period_start=2027-02-01&period_end=2027-02-28")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Recalculate & save snapshot" }));
  expect(await screen.findByText(/Capacity snapshot CAP-2027-02-002 saved/)).toBeInTheDocument();
  expect(calls.find((c) => c.url === "/api/capacity/recalculate")?.body).toEqual({ period_start: "2027-02-01", period_end: "2027-02-28" });
});

it("normalizes capacity rows in either shape", () => {
  expect(normalizeRow(CAPACITY.rows[1]).demand).toBe(9000);
  expect(normalizeRow(CAPACITY.rows[1]).status).toBe("Within Plan");
});
