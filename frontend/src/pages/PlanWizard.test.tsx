import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AppProvider } from "../lib/store";
import type { CapacityStatus, PlanDetail, PlanWorkOrder } from "../lib/types";
import PlanWizard, { forecastInPeriod, missingResources } from "./PlanWizard";

const cap = (status: CapacityStatus, extra = {}) => ({
  total: 23880,
  planning: 19104,
  buffer: 4776,
  gap: 0,
  utilization_pct: 95,
  status,
  contributions: [
    { machine_id: "IMM-04", available_minutes: 33120, cycle_s: 166, capacity: 11970 },
    { machine_id: "IMM-05", available_minutes: 33120, cycle_s: 167, capacity: 11910 },
  ],
  uncomputed: [{ machine_id: "IMM-06", reason: "inactive (planned maintenance)" }],
  ...extra,
});
const wo = (id: string, extra: Partial<PlanWorkOrder> = {}): PlanWorkOrder => ({
  wo_id: id,
  plan_id: "p1",
  line_id: "L1",
  sku: "FB-ARKA",
  process: "Molding",
  sequence: 1,
  predecessor_wo_id: null,
  qty: 22000,
  machine_id: "IMM-04",
  mold_id: "MOLD-FB-ARKA-1",
  planned_start: "2027-02-01T06:00:00Z",
  planned_end: "2027-02-10T14:00:00Z",
  shift: "A",
  status: "Draft",
  confirmed: false,
  ...extra,
});
const steps = (ready: number, blocked: Record<number, string> = {}) =>
  ["Demand", "Production Plan", "Work Order", "Scheduling", "Resource", "Confirmation"].map((name, i) => ({ step: i + 1, name, ready: i + 1 <= ready, blocked_by: blocked[i + 1] ?? null }));

const BASE: PlanDetail = {
  plan_id: "p1",
  plan_number: "PP-2027-02",
  period_start: "2027-02-01",
  period_end: "2027-02-28",
  status: "Planning",
  utilization_pct: 80,
  forecast_id: "FC-2027-02-001",
  created_at: "2027-01-28T08:00:00Z",
  created_by: "You",
  wizard_step: 2,
  origin: "user",
  lines: [
    { line_id: "L1", sku: "FB-ARKA", product_name: "Front bumper ARKA", demand_qty: 22000, forecast_qty: 0, forecast_line_ids: [], planned_qty: 22000, priority: 1, required_date: "2027-02-05", status: "Open", capacity: cap("Additional Demand") },
  ],
  demand: [
    { order_id: "SO-1", order_number: "CO-270201-OEMA", line_no: 1, customer_id: "CUS-OEMA", customer_name: "Nusantara Motor (OEM-A)", sku: "FB-ARKA", qty: 20000, requested_delivery_date: "2027-02-05", line_id: "L1" },
    { order_id: "SO-2", order_number: "CO-270205-TIER1", line_no: 1, customer_id: "CUS-TIER1", customer_name: "Sinar Komponen Tier-1", sku: "FB-ARKA", qty: 2000, requested_delivery_date: "2027-02-12", line_id: "L1" },
  ],
  readiness: { steps: steps(2, { 3: "Save the planned quantities first.", 4: "Generate work orders first.", 5: "Generate work orders first.", 6: "Generate work orders first." }), resume_step: 2 },
  work_orders: [],
  summary: { lines: 1, total_planned: 22000, work_orders: 0, customer_orders: 2 },
};
const CANDIDATES = {
  candidates: [
    { order_id: "SO-1", order_number: "CO-270201-OEMA", line_no: 1, customer_id: "CUS-OEMA", customer_name: "Nusantara Motor (OEM-A)", sku: "FB-ARKA", ordered_qty: 20000, planned_qty: 20000, remaining_qty: 0, requested_delivery_date: "2027-02-05" },
    { order_id: "SO-3", order_number: "CO-270208-EXP", line_no: 2, customer_id: "CUS-EXP", customer_name: "Selat Export Trading", sku: "RB-ARKA", ordered_qty: 900, planned_qty: 300, remaining_qty: 600, requested_delivery_date: "2027-02-08" },
  ],
};
const SNAPSHOT = {
  forecast_id: "FC-2027-02-001",
  lines: [
    { sku: "FB-ARKA", week_start: "2027-01-25", forecast_qty: 5000 }, // ends 2027-01-31, outside the period
    { sku: "FB-ARKA", week_start: "2027-02-01", forecast_qty: 6000 },
    { sku: "FB-ARKA", week_start: "2027-02-22", forecast_qty: 6000 },
    { sku: "FB-ARKA", week_start: "2027-03-01", forecast_qty: 9999 }, // after the period
    { sku: "RB-ARKA", week_start: "2027-02-08", forecast_qty: 800 },
  ],
};

let plan: PlanDetail = BASE;
const calls: { url: string; method: string; body?: unknown }[] = [];
const fail = (code: string, title: string, message: string) => ({ ok: false, status: 422, statusText: "Unprocessable", json: async () => ({ error: { code, title, message } }) });

function mockApi(over: (url: string, method: string) => unknown = () => undefined) {
  calls.length = 0;
  globalThis.fetch = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
    const o = over(url, method);
    if (o && typeof o === "object" && "ok" in (o as object)) return o;
    let body: unknown = o ?? { pending: 0, actions: [] };
    if (o === undefined) {
      if (url.startsWith("/api/mode")) body = { ai_enabled: false };
      else if (url.endsWith("/demand-candidates")) body = CANDIDATES;
      else if (url.startsWith("/api/forecasts/")) body = SNAPSHOT;
      else if (url.endsWith("/work-orders") && method === "POST") body = { created: [wo("WO-B")], existed: ["WO-A"], work_orders: [] };
      else if (url.endsWith("/schedule")) body = { status: "OPTIMAL", solve_seconds: 1.234, work_orders: [] };
      else if (url === "/api/plans/p1") body = plan;
      else if (url.startsWith("/api/plans/")) body = {};
    }
    return { ok: true, json: async () => body };
  });
}

function renderWizard() {
  render(
    <MemoryRouter initialEntries={["/planning/p1"]}>
      <AppProvider>
        <Routes>
          <Route path="/planning/:planId" element={<PlanWizard />} />
        </Routes>
      </AppProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  plan = BASE;
});

it("resumes at the server's step, shows what blocks the next one, and allows going back", async () => {
  mockApi();
  renderWizard();
  expect(await screen.findByRole("heading", { name: /PP-2027-02/ })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Step 2 — Production Plan" })).toBeInTheDocument();
  expect(screen.getByText("Next step locked: Save the planned quantities first.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Next ›" }));
  expect(screen.getByRole("alert")).toHaveTextContent("Save the planned quantities first.");
  expect(screen.getByRole("heading", { name: "Step 2 — Production Plan" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /4\. Scheduling/ })).toHaveAttribute("title", "Generate work orders first.");
  fireEvent.click(screen.getByRole("button", { name: "‹ Previous" }));
  expect(screen.getByRole("heading", { name: "Step 1 — Demand" })).toBeInTheDocument();
  expect(calls.some((c) => c.url.endsWith("/step"))).toBe(false); // going back is never stored
});

it("stores forward progress when a ready step is opened", async () => {
  plan = { ...BASE, readiness: { steps: steps(3), resume_step: 2 } };
  mockApi();
  renderWizard();
  await screen.findByRole("heading", { name: "Step 2 — Production Plan" });
  fireEvent.click(screen.getByRole("button", { name: "Next ›" }));
  expect(screen.getByRole("heading", { name: "Step 3 — Work Order" })).toBeInTheDocument();
  await waitFor(() => expect(calls.find((c) => c.url === "/api/plans/p1/step")?.body).toEqual({ step: 3 }));
});

it("step 1: demand breakdown per line, open demand with the remainder as default, add, release and forecast as demand", async () => {
  plan = { ...BASE, readiness: { ...BASE.readiness, resume_step: 1 } };
  mockApi();
  renderWizard();
  expect(await screen.findByRole("heading", { name: "Step 1 — Demand" })).toBeInTheDocument();
  expect(screen.getByText("Selected demand (2)")).toBeInTheDocument();
  expect(screen.getByText("CO-270205-TIER1")).toBeInTheDocument();
  // the fully planned line SO-1 is already in the plan and not offered
  expect(await screen.findByText("Open demand (1)")).toBeInTheDocument();
  const qty = screen.getByLabelText("Quantity for CO-270208-EXP");
  expect(qty).toHaveValue(600);
  fireEvent.change(qty, { target: { value: "250" } });
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  await waitFor(() => expect(calls.find((c) => c.url === "/api/plans/p1/demand" && c.method === "POST")?.body).toEqual({ order_id: "SO-3", qty: 250 }));
  expect(await screen.findByText("CO-270208-EXP added to the plan.")).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "Release" })[1]);
  await waitFor(() => expect(calls.some((c) => c.url === "/api/plans/p1/demand/SO-2" && c.method === "DELETE")).toBe(true));
  // Forecast in the period: 6000 + 6000 for FB-ARKA, all covered by the 22,000 order demand
  expect(await screen.findByText("12,000")).toBeInTheDocument();
  const rb = screen.getByLabelText("Forecast quantity for RB-ARKA");
  expect(rb).toHaveValue(800);
  fireEvent.click(screen.getAllByRole("button", { name: "Use as demand" })[1]);
  await waitFor(() => expect(calls.find((c) => c.url === "/api/plans/p1/forecast-demand")?.body).toEqual({ sku: "RB-ARKA", qty: 800 }));
});

it("step 1: shows the server's rejection of a demand", async () => {
  plan = { ...BASE, readiness: { ...BASE.readiness, resume_step: 1 } };
  mockApi((url, method) => (url === "/api/plans/p1/demand" && method === "POST" ? fail("FV-PP-006", "Order cancelled", "Order CO-270208-EXP is Cancelled and cannot be planned.") : undefined));
  renderWizard();
  fireEvent.click(await screen.findByRole("button", { name: "Add" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Order cancelled (FV-PP-006): Order CO-270208-EXP is Cancelled and cannot be planned.");
});

it("step 2: planned and priority editor with capacity, contributions and uncomputed machines", async () => {
  mockApi();
  renderWizard();
  const editor = await screen.findByRole("article", { name: "Plan line FB-ARKA" });
  expect(within(editor).getByText("Additional Demand")).toBeInTheDocument();
  expect(within(editor).getByText("IMM-05")).toBeInTheDocument();
  expect(within(editor).getByText(/inactive \(planned maintenance\)/)).toBeInTheDocument();
  expect(within(editor).getByTitle("Total × 80%")).toHaveTextContent("Planning 19,104");
  const save = within(editor).getByRole("button", { name: "Save" });
  expect(save).toBeDisabled();
  fireEvent.change(within(editor).getByLabelText("Planned quantity for FB-ARKA"), { target: { value: "-5" } });
  expect(within(editor).getByText("Planned quantity cannot be negative.")).toBeInTheDocument();
  expect(save).toBeDisabled();
  fireEvent.change(within(editor).getByLabelText("Planned quantity for FB-ARKA"), { target: { value: "19000" } });
  expect(within(editor).getByText(/3,000 below demand 22,000/)).toBeInTheDocument();
  fireEvent.change(within(editor).getByLabelText("Priority for FB-ARKA"), { target: { value: "2" } });
  fireEvent.click(save);
  await waitFor(() => expect(calls.find((c) => c.method === "PATCH")).toEqual({ url: "/api/plans/p1/lines/L1", method: "PATCH", body: { planned_qty: 19000, priority: 2 } }));
});

it("step 3: generate reports created versus already existing work orders", async () => {
  plan = { ...BASE, readiness: { steps: steps(3), resume_step: 3 }, work_orders: [wo("WO-A"), wo("WO-B", { process: "Painting", sequence: 2, predecessor_wo_id: "WO-A", machine_id: null, mold_id: null })] };
  mockApi();
  renderWizard();
  expect(await screen.findByRole("heading", { name: "Step 3 — Work Order" })).toBeInTheDocument();
  expect(screen.getAllByText("WO-A")).toHaveLength(2); // its own row and WO-B's predecessor
  fireEvent.click(screen.getByRole("button", { name: "Generate work orders" }));
  expect(await screen.findByText("1 work order created, 1 already existed.")).toBeInTheDocument();
  expect(screen.getByText("Already existed: WO-A")).toBeInTheDocument();
});

it("step 4: runs CP-SAT and draws the schedule per machine", async () => {
  plan = { ...BASE, readiness: { steps: steps(4), resume_step: 4 }, work_orders: [wo("WO-A"), wo("WO-C", { line_id: "L1", machine_id: "IMM-05", planned_start: "2027-02-11T06:00:00Z", planned_end: "2027-02-14T06:00:00Z" })] };
  mockApi();
  renderWizard();
  expect(await screen.findByRole("img", { name: "Schedule of 2 work orders on 2 machines" })).toBeInTheDocument();
  expect(screen.getAllByTitle(/WO-A · Molding · 22,000 pcs · 2027-02-01 06:00 → 2027-02-10 14:00 · shift A/)).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Run scheduler (CP-SAT)" }));
  expect(await screen.findByText(/in 1.23 s/)).toBeInTheDocument();
  expect(screen.getByText("OPTIMAL")).toBeInTheDocument();
});

it("step 5: offers only compatible machines, counts missing resources, and confirms work orders", async () => {
  plan = { ...BASE, readiness: { steps: steps(5, { 6: "2 work orders have no machine or mold." }), resume_step: 5 }, work_orders: [wo("WO-A", { machine_id: null }), wo("WO-B", { mold_id: null })] };
  mockApi();
  renderWizard();
  expect(await screen.findByText(/2 work orders have no machine and\/or mold yet/)).toBeInTheDocument();
  const machine = screen.getByLabelText("Machine for WO-A");
  expect(within(machine).getAllByRole("option").map((o) => o.textContent)).toEqual(["Not set", "IMM-04", "IMM-05"]);
  expect(screen.getByRole("button", { name: /Confirm work orders/ })).toBeDisabled();
  fireEvent.change(machine, { target: { value: "IMM-05" } });
  await waitFor(() => expect(calls.find((c) => c.method === "PATCH")).toEqual({ url: "/api/plans/p1/work-orders/WO-A", method: "PATCH", body: { machine_id: "IMM-05" } }));
  fireEvent.click(screen.getByRole("button", { name: /6\. Confirmation/ }));
  expect(screen.getByRole("alert")).toHaveTextContent("2 work orders have no machine or mold.");
});

it("step 6: summary, warnings, and the server's reason when confirm is refused", async () => {
  plan = {
    ...BASE,
    lines: [{ ...BASE.lines[0], capacity: cap("Capacity Up Required", { gap: 2000 }) }],
    readiness: { steps: steps(6), resume_step: 6 },
    work_orders: [wo("WO-A", { confirmed: true, status: "Confirmed" }), wo("WO-B")],
    summary: { lines: 1, total_planned: 22000, work_orders: 2, customer_orders: 2 },
  };
  mockApi((url) => (url.endsWith("/confirm") ? fail("FV-PP-020", "Plan cannot be confirmed", "Line FB-ARKA is Capacity Up Required.") : undefined));
  renderWizard();
  expect(await screen.findByRole("heading", { name: "Step 6 — Confirmation" })).toBeInTheDocument();
  expect(screen.getByText(/1 plan line is Capacity Up Required \(FB-ARKA\)/)).toBeInTheDocument();
  expect(screen.getByText(/1 work order is not confirmed yet/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Confirm production plan" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Plan cannot be confirmed (FV-PP-020): Line FB-ARKA is Capacity Up Required.");
});

it("cancels with a reason of at least 3 characters and shows a refusal", async () => {
  mockApi((url) => (url.endsWith("/cancel") ? fail("FV-PP-024", "Plan cannot be cancelled", "2 work orders are In Production or Completed.") : undefined));
  renderWizard();
  fireEvent.click(await screen.findByRole("button", { name: "Cancel plan…" }));
  const dialog = screen.getByRole("dialog", { name: "Cancel PP-2027-02" });
  const submit = within(dialog).getByRole("button", { name: "Cancel plan" });
  fireEvent.change(within(dialog).getByLabelText("Reason"), { target: { value: "no" } });
  expect(within(dialog).getByText("Give a reason of at least 3 characters.")).toBeInTheDocument();
  expect(submit).toBeDisabled();
  fireEvent.change(within(dialog).getByLabelText("Reason"), { target: { value: "Customer moved the launch" } });
  fireEvent.click(submit);
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("2 work orders are In Production or Completed.");
  expect(calls.find((c) => c.url.endsWith("/cancel"))?.body).toEqual({ reason: "Customer moved the launch" });
});

it("locks a confirmed plan and explains why; marks an agent plan", async () => {
  plan = { ...BASE, status: "Confirmed", origin: "agent", confirmed_by: "You", confirmed_at: "2027-02-01T09:00:00Z", readiness: { steps: steps(6), resume_step: 6 } };
  mockApi();
  renderWizard();
  expect(await screen.findByText(/This plan is Confirmed and can no longer be changed/)).toBeInTheDocument();
  expect(screen.getByText("Agent")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Confirm production plan" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Cancel plan…" })).not.toBeInTheDocument();
});

it("shows an error when the plan cannot be loaded", async () => {
  mockApi((url) => (url === "/api/plans/p1" ? { ok: false, status: 404, statusText: "Not Found", json: async () => ({ error: { code: "FV-PP-003", title: "Plan not found", message: "No plan p1" } }) } : undefined));
  renderWizard();
  expect(await screen.findByText("The production plan could not be loaded.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "← Production plans" })).toHaveAttribute("href", "/planning");
});

it("helpers: forecast weeks overlapping the period, and missing resources", () => {
  expect(forecastInPeriod(SNAPSHOT, "2027-02-01", "2027-02-28")).toEqual({ "FB-ARKA": 12000, "RB-ARKA": 800 });
  expect(missingResources([wo("a", { machine_id: null }), wo("b", { process: "Assembly", mold_id: null }), wo("c", { mold_id: null })]).map((w) => w.wo_id)).toEqual(["a", "c"]);
});
