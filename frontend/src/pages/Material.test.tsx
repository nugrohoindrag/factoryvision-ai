import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { formatIdrCompact } from "../lib/format";
import { AppProvider } from "../lib/store";
import { MaterialPage } from "./Material";

const OVERVIEW = {
  from: "2027-02-01",
  to: "2027-03-03",
  kpis: { readiness_pct: 62.5, plans_monitored: 2, plans_short: 1, materials_short: 3 },
  plans: [
    { plan_id: "p1", plan_number: "PP-2027-02", period_start: "2027-02-01", period_end: "2027-02-28", status: "PARTIAL", readiness_pct: 50, short_count: 3, checked_at: "2027-02-01T08:00:00Z", origin: "user" },
    { plan_id: "OEM-2027-02", plan_number: "OEM monthly plan 2027-02", period_start: "2027-02-01", period_end: "2027-02-28", status: "READY", readiness_pct: 100, short_count: 0, checked_at: null, origin: "oem" },
  ],
};
const row = (extra = {}) => ({
  material_id: "B-C07",
  material_name: "Basecoat C07",
  material_group: "Basecoat",
  color: "C07",
  unit: "kg",
  required_qty: 8580.5,
  required_date: "2027-02-03",
  on_hand_qty: 3000,
  reserved_qty: 200,
  incoming_qty: 1500,
  incoming_counted_qty: 1000,
  incoming: [
    { po_id: "PO-1", qty: 1000, expected_date: "2027-02-02", counted: true },
    { po_id: "PO-2", qty: 500, expected_date: "2027-02-09", counted: false },
  ],
  available_qty: 3800,
  shortage_qty: 4780.5,
  status: "PARTIAL",
  at_risk: false,
  note: "PO-2 arrives after need date",
  ...extra,
});
const CHECK = {
  plan_id: "p1",
  plan_number: "PP-2027-02",
  wo_id: null,
  status: "PARTIAL",
  readiness_pct: 50,
  required_date: "2027-02-03",
  checked_at: "2027-02-01T08:00:00Z",
  materials: [row(), row({ material_id: "PP-EPDM-T20", material_name: "Resin", material_group: "Resin", color: null, incoming: [], incoming_qty: 0, incoming_counted_qty: 0, shortage_qty: 0, status: "READY", at_risk: true, note: null })],
  shortages: [row()],
};
const PLAN = { plan_id: "p1", plan_number: "PP-2027-02", work_orders: [{ wo_id: "WO-A", process: "Painting", planned_start: "2027-02-03T06:00:00Z" }] };
const res = (material_id: string, net: number) => ({
  material_id,
  material_name: material_id,
  color: null,
  unit: "kg",
  gross_qty: 1000 + net,
  available_qty: 1000,
  net_qty: net,
  required_date: "2027-02-05",
  order_by_date: "2027-01-29",
  lead_time_days: 7,
  default_vendor_id: "VEN-01",
  recommendation: net > 0 ? `Procure ${net} kg before 2027-01-29` : null,
});
const RUN = {
  run_id: "MRP-2027-02-001",
  horizon_start: "2027-02-01",
  horizon_end: "2027-03-03",
  plan_ids: [],
  created_at: "2027-02-01T09:00:00Z",
  created_by: "You",
  total_materials: 3,
  shortage_materials: 2,
  results: [res("MB-BLK", 0), res("B-C07", 500), res("BRK-ARKA-F", 1200)],
};
const INVENTORY = {
  source: "fv.fact_material_stock",
  as_of: "2027-02-01",
  rows: [
    { material_id: "BRK-ARKA-F", material_name: "Bracket ARKA front", material_group: "Component", color: null, unit: "set", warehouse_id: "WH-CMP", on_hand_qty: 300, reserved_qty: 50, incoming_qty: 400, incoming: [{ po_id: "PO-9", material_id: "BRK-ARKA-F", vendor_id: "VEN-03", qty: 400, promised_date: "2027-02-04", expected_date: "2027-02-06", avg_delay_days: 2, source: "open_po" }], available_qty: 650, reorder_point: 500, safety_stock: 200, below_reorder: true },
    { material_id: "B-C07", material_name: "Basecoat C07", material_group: "Basecoat", color: "C07", unit: "kg", warehouse_id: "WH-PNT", on_hand_qty: 3000, reserved_qty: 0, incoming_qty: 0, incoming: [], available_qty: 3000, reorder_point: 1000, safety_stock: 500, below_reorder: false },
  ],
  kpis: { materials: 2, below_reorder: 1, incoming_pos: 1, value_idr: 125000000 },
};
const RESERVATIONS = { reservations: [{ reservation_id: "RSV-1", plan_id: "p1", wo_id: "WO-A", material_id: "B-C07", warehouse_id: "WH-PNT", qty: 120, status: "Active", created_at: "2027-02-01T09:00:00Z", released_at: null }] };
const CONSUMPTION = {
  tolerance_pct: 2,
  rows: [
    { wo_id: "WO-A", material_id: "PP-EPDM-T20", unit: "kg", planned_qty: 1000, actual_qty: 1080, variance_qty: 80, variance_pct: 8, status: "OVER_CONSUMPTION" },
    { wo_id: "WO-A", material_id: "MB-BLK", unit: "kg", planned_qty: 30, actual_qty: 30, variance_qty: 0, variance_pct: 0, status: "NORMAL" },
    { wo_id: "WO-A", material_id: "P1", unit: "kg", planned_qty: 50, actual_qty: 45, variance_qty: -5, variance_pct: -10, status: "UNDER_CONSUMPTION" },
  ],
};
const LEDGER = { transactions: [{ txn_id: "TX-1", date: "2027-02-01", material_id: "B-C07", warehouse_id: "WH-PNT", type: "Reserve", qty: -120, balance_after: 2880, ref: "RSV-1" }] };
const item = (material_id: string, process: string, color: string | null = null) => ({ bom_id: "BOM-2027-002", material_id, material_name: `Name of ${material_id}`, material_group: "x", qty_per_unit: 0.3, unit: "kg", scrap_pct: 30, process, color });
const BOMS = {
  boms: [
    { bom_id: "BOM-2027-002", sku: "FB-ARKA", version: 2, status: "Active", effective_date: "2027-01-01", items: [item("PP-EPDM-T20", "Molding"), item("B-C07", "Painting", "C07"), item("B-C02", "Painting", "C02"), item("BRK-ARKA-F", "Assembly")] },
    { bom_id: "BOM-2027-001", sku: "FB-ARKA", version: 1, status: "Inactive", effective_date: "2026-06-01", items: [] },
    { bom_id: "BOM-2027-003", sku: "RB-ARKA", version: 1, status: "Active", effective_date: "2027-01-01", items: [] },
  ],
};

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
      else if (url.startsWith("/api/materials/readiness/plans/")) body = CHECK;
      else if (url.startsWith("/api/materials/readiness")) body = OVERVIEW;
      else if (url.startsWith("/api/plans/p1")) body = PLAN;
      else if (url.includes("/propose-po")) body = { action: { action_id: "A-1", status: "pending", kind: "purchase_order" } };
      else if (url === "/api/mrp/runs" && method === "POST") body = RUN;
      else if (url === "/api/mrp/runs/latest") body = null;
      else if (url === "/api/mrp/runs") body = { runs: [RUN] };
      else if (url.startsWith("/api/mrp/runs/")) body = RUN;
      else if (url.startsWith("/api/inventory/transactions")) body = LEDGER;
      else if (url.startsWith("/api/inventory")) body = INVENTORY;
      else if (url.startsWith("/api/master/warehouses")) body = { warehouses: [{ warehouse_id: "WH-CMP", name: "Components" }, { warehouse_id: "WH-PNT", name: "Paint store" }] };
      else if (url.startsWith("/api/master/materials")) body = { materials: [{ material_id: "B-C07", material_name: "Basecoat C07" }], groups: ["Basecoat", "Component", "Resin"] };
      else if (url.startsWith("/api/master/colors")) body = { models: { ARKA: ["C02", "C07"] }, skus: { "FB-ARKA": ["C02", "C07"], "RB-ARKA": ["C02", "C07"] }, colors: [] };
      else if (url.startsWith("/api/master/boms")) body = BOMS;
      else if (url.endsWith("/release")) body = { ...RESERVATIONS.reservations[0], status: "Released" };
      else if (url.startsWith("/api/materials/reservations") && method === "POST") body = RESERVATIONS;
      else if (url.startsWith("/api/materials/reservations")) body = RESERVATIONS;
      else if (url.startsWith("/api/materials/consumption")) body = CONSUMPTION;
      else if (url.startsWith("/api/modules/material")) body = { resin: null, requirements: null };
    }
    return { ok: true, json: async () => body };
  });
}

function renderPage(path = "/material") {
  render(
    <MemoryRouter initialEntries={[path]}>
      <AppProvider>
        <MaterialPage />
      </AppProvider>
    </MemoryRouter>,
  );
}

it("readiness: KPIs, plan table, the first plan's detail with dated incoming, formula and Check now", async () => {
  mockApi();
  renderPage();
  expect(await screen.findByRole("heading", { name: "Material Supply" })).toBeInTheDocument();
  expect(await screen.findByText("63%")).toBeInTheDocument();
  expect(screen.getByText("2027-02-01 → 2027-03-03")).toBeInTheDocument();
  expect(screen.getByLabelText("Readiness from")).toHaveValue("2027-02-01");
  const plans = screen.getByRole("table", { name: "Plans" });
  expect(within(plans).getByText("OEM monthly plan 2027-02")).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "Material requirements: PP-2027-02" })).toBeInTheDocument();
  expect(screen.getByText("Available = On Hand − Reserved + Incoming")).toBeInTheDocument();
  const req = screen.getByRole("table", { name: "Material requirements" });
  expect(within(req).getAllByText("8,580.5 kg")).toHaveLength(2);
  expect(within(req).getByText("4,780.5")).toBeInTheDocument();
  expect(within(req).getAllByText("arrives after need date")).toHaveLength(1);
  expect(within(req).getByText("At risk")).toBeInTheDocument();
  expect(within(req).getByText("counted 1,000 of 1,500")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Check now" }));
  await waitFor(() => expect(calls.some((c) => c.url === "/api/materials/readiness/plans/p1" && c.method === "POST")).toBe(true));
  fireEvent.click(within(plans).getByRole("button", { name: "Details of OEM monthly plan 2027-02" }));
  await waitFor(() => expect(calls.some((c) => c.url === "/api/materials/readiness/plans/OEM-2027-02")).toBe(true));
  fireEvent.click(screen.getByLabelText("Only problems"));
  await waitFor(() => expect(calls.some((c) => c.url.includes("only_problems=true"))).toBe(true));
  expect(within(screen.getByRole("table", { name: "Plans" })).queryByText("OEM monthly plan 2027-02")).not.toBeInTheDocument();
});

it("readiness: checks one work order of a session plan", async () => {
  mockApi();
  renderPage("/material?plan=p1");
  fireEvent.change(await screen.findByLabelText("Work order to check"), { target: { value: "WO-A" } });
  fireEvent.click(screen.getByRole("button", { name: "Check work order" }));
  await waitFor(() => expect(calls.some((c) => c.url === "/api/materials/readiness/plans/p1/work-orders/WO-A" && c.method === "POST")).toBe(true));
});

it("readiness: empty range invites a new plan; an error offers Retry", async () => {
  let n = 0;
  mockApi((url) => {
    if (!url.startsWith("/api/materials/readiness")) return undefined;
    n += 1;
    return n === 1 ? fail("FV-SYS-001", "Internal error", "warehouse down") : { ...OVERVIEW, plans: [], kpis: { ...OVERVIEW.kpis, plans_monitored: 0 } };
  });
  renderPage();
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("warehouse down");
  fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("No production plan in this range")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "+ Create a plan" }));
  expect(screen.getByRole("dialog", { name: "New production plan" })).toBeInTheDocument();
});

it("MRP: validates the horizon, runs, sorts by net, and sends a row to Purchasing as a pending action", async () => {
  mockApi();
  renderPage("/material?tab=mrp");
  expect(await screen.findByText("No MRP run yet")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Horizon start"), { target: { value: "2027-02-10" } });
  fireEvent.change(screen.getByLabelText("Horizon end"), { target: { value: "2027-02-01" } });
  expect(screen.getByText("The horizon end cannot be before the start.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Run MRP" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Horizon end"), { target: { value: "2027-03-01" } });
  fireEvent.click(await screen.findByLabelText("PP-2027-02"));
  fireEvent.click(screen.getByRole("button", { name: "Run MRP" }));
  expect(await screen.findByText("Results MRP-2027-02-001")).toBeInTheDocument();
  expect(calls.find((c) => c.url === "/api/mrp/runs" && c.method === "POST")?.body).toEqual({ horizon_start: "2027-02-10", horizon_end: "2027-03-01", plan_ids: ["p1"] });
  const sends = screen.getAllByRole("button", { name: /^Send .* to Purchasing$/ });
  expect(sends.map((b) => b.getAttribute("aria-label"))).toEqual(["Send BRK-ARKA-F to Purchasing", "Send B-C07 to Purchasing"]);
  expect(screen.getByText("Procure 1200 kg before 2027-01-29")).toBeInTheDocument();
  fireEvent.click(sends[0]);
  expect(await screen.findByText("Pending approval")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Action Center" })).toHaveAttribute("href", "/actions");
  expect(calls.find((c) => c.url.endsWith("/propose-po"))).toMatchObject({ url: "/api/mrp/runs/MRP-2027-02-001/propose-po", method: "POST", body: { material_id: "BRK-ARKA-F" } });
  expect(screen.getByRole("button", { name: "View MRP-2027-02-001" })).toBeInTheDocument();
});

it("MRP: shows the server's refusal", async () => {
  mockApi((url, method) => (url === "/api/mrp/runs" && method === "POST" ? fail("FV-MRP-001", "Invalid MRP horizon", "The horizon end is before its start.") : undefined));
  renderPage("/material?tab=mrp");
  fireEvent.click(await screen.findByRole("button", { name: "Run MRP" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Invalid MRP horizon (FV-MRP-001): The horizon end is before its start.");
});

it("inventory: stock with filters, the below-reorder badge and incoming; then reservations, consumption and ledger", async () => {
  mockApi();
  renderPage("/material?tab=inventory");
  expect(await screen.findByText("Below reorder")).toBeInTheDocument();
  expect(screen.getByText(formatIdrCompact(125000000))).toBeInTheDocument();
  expect(screen.getByText("PO-9")).toBeInTheDocument();
  expect(screen.getByText(/fv\.fact_material_stock · as of 2027-02-01/)).toBeInTheDocument();
  expect(await screen.findByRole("option", { name: "Resin" })).toBeInTheDocument();
  expect(screen.getByText(/\+2 d vendor delay/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Filter by group"), { target: { value: "Component" } });
  fireEvent.change(await screen.findByLabelText("Filter by warehouse"), { target: { value: "WH-CMP" } });
  await waitFor(() => expect(calls.some((c) => c.url === "/api/inventory?group=Component&warehouse_id=WH-CMP")).toBe(true));

  fireEvent.click(screen.getByRole("tab", { name: "Reservations" }));
  expect(await screen.findByText("RSV-1")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Plan to reserve for"), { target: { value: "p1" } });
  fireEvent.change(await screen.findByLabelText("Work order to reserve for"), { target: { value: "WO-A" } });
  await waitFor(() => expect(screen.getByLabelText("Work order to reserve for")).toHaveValue("WO-A"));
  fireEvent.click(screen.getByRole("button", { name: "Reserve material" }));
  expect(await screen.findByText(/reservation\(s\) for WO-A are active/)).toBeInTheDocument();
  expect(calls.find((c) => c.url === "/api/materials/reservations" && c.method === "POST")?.body).toEqual({ plan_id: "p1", wo_id: "WO-A" });
  fireEvent.click(screen.getByRole("button", { name: "Release RSV-1" }));
  expect(await screen.findByText(/RSV-1 released/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("tab", { name: "Consumption" }));
  expect(await screen.findByText("Over")).toBeInTheDocument();
  expect(screen.getByText("Normal")).toBeInTheDocument();
  expect(screen.getByText("Under")).toBeInTheDocument();
  expect(screen.getByText(/within ±2% is Normal/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("tab", { name: "Transactions" }));
  expect(await screen.findByText("TX-1")).toBeInTheDocument();
  expect(screen.getByText("2,880")).toBeInTheDocument();
  expect(screen.getByText("-120")).toBeInTheDocument();
});

it("BOM: picks SKU and colour, shows the active items for that colour and the versions", async () => {
  mockApi();
  renderPage("/material?tab=bom");
  expect((await screen.findAllByText("BOM-2027-002")).length).toBe(2);
  expect(screen.getByText("B-C02")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("BOM colour"), { target: { value: "C07" } });
  expect(screen.queryByText("B-C02")).not.toBeInTheDocument();
  expect(screen.getByText("B-C07")).toBeInTheDocument();
  expect(screen.getAllByText("all colours").length).toBe(2);
  expect(screen.getAllByText("30%").length).toBeGreaterThan(0);
  expect(screen.getByText("Inactive")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("BOM product"), { target: { value: "RB-ARKA" } });
  expect(screen.getByRole("heading", { name: "Versions of RB-ARKA" })).toBeInTheDocument();
});
