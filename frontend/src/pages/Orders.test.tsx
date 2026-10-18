import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AppProvider } from "../lib/store";
import OrdersPage from "./Orders";

const SUMMARY = {
  module: "orders",
  customer_pos: [],
  customer_po_change_pct: {},
  book: {
    as_of: "2027-02-01",
    statuses: ["Received", "Planned", "In Production", "Produced", "Ready to Ship", "Shipped", "Completed", "Cancelled"],
    status_counts: { Received: 8, Planned: 4, "In Production": 4, Completed: 66 },
    open_count: 16,
    open_qty: 23671,
    at_risk_count: 3,
    due_this_week: 16,
    on_time_delivery_pct: 97.9,
    shipments_30d: 240,
    open_by_customer: [{ customer_id: "CUS-OEMA", customer_name: "Nusantara Motor (OEM-A)", open_qty: 12000 }],
    source: "fv.fact_customer_order",
  },
};

const WO = { wo_id: "WO-270201-IMM-04-FB-ARKA", date: "2027-02-01", machine_id: "IMM-04", sku: "FB-ARKA", qty: 499, status: "In Progress" };
const line = (sku: string, extra = {}) => ({
  order_id: `SO-${sku}`,
  line_no: 1,
  sku,
  product_name: `${sku} bumper`,
  ordered_qty: 900,
  planned_qty: 900,
  produced_qty: 400,
  from_stock_qty: 400,
  requested_delivery_date: "2027-02-01",
  stored_status: "Planned",
  status: "In Production",
  work_orders: [WO],
  at_risk: false,
  estimated_completion: null,
  days_late: 0,
  ...extra,
});
const ORDER_A = {
  order_number: "CO-270201-OEMA",
  customer_id: "CUS-OEMA",
  customer_name: "Nusantara Motor (OEM-A)",
  po_number: "KB-OEMA-270201",
  order_channel: "KANBAN_CARD",
  channel_label: "Kanban",
  order_date: "2027-01-25",
  requested_delivery_date: "2027-02-01",
  customer_pic: "Supplier call-off desk",
  delivery_address: "Nusantara Motor Plant 2, Karawang",
  dock_number: "D-07",
  created_in_session: false,
  created_by: null,
  status_reason: null,
  status: "In Production",
  ordered_qty: 900,
  planned_qty: 900,
  produced_qty: 400,
  progress_pct: 44,
  at_risk: false,
  days_late: 0,
  estimated_completion: null,
  penalty_idr: 0,
  cancellable: false,
  cancel_block_reason: "Work order WO-270201-IMM-04-FB-ARKA is already in production for this order.",
  lines: [line("FB-ARKA", { color: "C07" })],
};
const ORDER_B = {
  ...ORDER_A,
  order_number: "CO-270205-TIER1",
  customer_id: "CUS-TIER1",
  customer_name: "Sinar Komponen Tier-1",
  po_number: "SK-PO-270205",
  requested_delivery_date: "2027-02-05",
  status: "Received",
  at_risk: true,
  days_late: 1,
  estimated_completion: "2027-02-06",
  cancellable: true,
  cancel_block_reason: null,
  lines: [line("RB-ARKA", { status: "Received", at_risk: true, days_late: 1, estimated_completion: "2027-02-06" })],
};
const LIST = {
  as_of: "2027-02-01",
  orders: [ORDER_A, ORDER_B],
  total: 222,
  statuses: SUMMARY.book.statuses,
  at_risk_rule: "cumulative open demand exceeds supply",
  source: "fv.fact_customer_order",
  customers: [
    { customer_id: "CUS-OEMA", customer_name: "Nusantara Motor (OEM-A)", pic_name: "Supplier call-off desk", delivery_address: "Karawang", dock_number: "D-07", default_channel: "KANBAN_CARD" },
    { customer_id: "CUS-TIER1", customer_name: "Sinar Komponen Tier-1", pic_name: "Purchasing", delivery_address: "Bekasi", dock_number: "R-1", default_channel: "PO_DOCUMENT" },
    { customer_id: "CUS-EXP", customer_name: "Selat Export Trading", pic_name: "Export desk", delivery_address: "Tanjung Priok", dock_number: null, default_channel: "PO_DOCUMENT" },
  ],
  products: [
    { sku: "FB-BROMO", name: "Front bumper BROMO (SUV)", unit: "pcs" },
    { sku: "RB-BROMO", name: "Rear bumper BROMO (SUV)", unit: "pcs" },
  ],
  channels: [
    { value: "PO_DOCUMENT", label: "Customer PO", reference_label: "Customer PO number", reference_required: true },
    { value: "KANBAN_CARD", label: "Kanban", reference_label: "Kanban reference", reference_required: true },
    { value: "MANUAL", label: "Manual", reference_label: "Reference number", reference_required: false },
  ],
};
const DETAIL = {
  ...ORDER_A,
  documents: [],
  traceability: [{ plan_number: "PP-2027-02", month: "2027-02", status: "In Execution", from_stock_qty: 400, lines: [{ sku: "FB-ARKA", machine_id: "IMM-04", plan_qty: 23880, order_qty: 900 }], work_orders: [WO] }],
};

const READ = {
  engine: "recorded extraction (Replay, no Gemini call)",
  extracted: { customer_name: "Nusantara Motor (OEM-A)", po_number: "NM-PO-2027-0142", lines: [] },
  check: {
    customer_id: "CUS-OEMA",
    customer_name: "Nusantara Motor (OEM-A)",
    po_number: "NM-PO-2027-0142",
    issue_date: "2027-01-31",
    notes: null,
    warnings: [],
    source: "fv.fact_monthly_plan",
    lines: [{ sku: "FB-ARKA", month: "2027-02", previous_qty: 19104, qty: 26746, change_pct: 40, per_working_day: 1114, on_file_qty: 19104, matches_on_file: true }],
    changed: [{ sku: "FB-ARKA", change_pct: 40 }],
  },
};

const calls: { url: string; method: string; body?: unknown }[] = [];

function mockApi() {
  calls.length = 0;
  globalThis.fetch = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
    let body: unknown = { pending: 0, actions: [] };
    if (url.includes("/api/orders/po/read")) body = READ;
    else if (url.includes("/api/master/colors")) body = { ARKA: ["C02", "C04", "C05", "C07", "C08", "C09"], BROMO: ["C01", "C02", "C04"] };
    else if (url.includes("/api/modules/orders")) body = SUMMARY;
    else if (url.includes("/api/mode")) body = { ai_enabled: false };
    else if (url.endsWith("/api/orders/validate")) body = { errors: [], warnings: ["Reference SET-PO-1 is already on order CO-1."], preview: { at_risk: false } };
    else if (url.endsWith("/api/orders") && method === "POST") body = { order: { ...ORDER_A, order_number: "CO-270212-EXP-AB12", customer_name: "Selat Export Trading", ordered_qty: 300 } };
    else if (url.startsWith("/api/orders/CO-")) body = DETAIL;
    else if (url.startsWith("/api/orders")) body = url.includes("customer_id=CUS-TIER1") ? { ...LIST, orders: [ORDER_B] } : LIST;
    return { ok: true, json: async () => body };
  });
}

function renderPage(path = "/orders") {
  render(
    <MemoryRouter initialEntries={[path]}>
      <AppProvider>
        <OrdersPage />
      </AppProvider>
    </MemoryRouter>,
  );
}

it("lists orders as headers, flags the late one, filters and expands lines", async () => {
  mockApi();
  renderPage();
  expect(await screen.findByRole("heading", { name: "Customer Orders" })).toBeInTheDocument();
  expect(await screen.findByText("97.9%")).toBeInTheDocument();
  expect(await screen.findByText("Customer orders (2)")).toBeInTheDocument();
  expect(screen.getByTitle(/At risk of being late: completes 2027-02-06/)).toBeInTheDocument();
  expect(screen.getAllByText("400 / 900 (44%)")).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "Lines of CO-270201-OEMA" }));
  expect(screen.getByText("FB-ARKA bumper")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Filter by customer"), { target: { value: "CUS-TIER1" } });
  expect(await screen.findByText("Customer orders (1 of 222)")).toBeInTheDocument();
  expect(calls.some((c) => c.url.includes("customer_id=CUS-TIER1"))).toBe(true);
});

it("pages the list with Previous and Next, and goes back to page 1 when a filter changes", async () => {
  mockApi();
  const many = Array.from({ length: 30 }, (_, i) => ({ ...ORDER_A, order_number: `CO-2702${String(i).padStart(2, "0")}-OEMA` }));
  const base = globalThis.fetch as ReturnType<typeof vi.fn>;
  const inner = base.getMockImplementation() as (url: string, init?: RequestInit) => Promise<unknown>;
  base.mockImplementation(async (url: string, init?: RequestInit) =>
    url.startsWith("/api/orders") && !url.startsWith("/api/orders/") ? { ok: true, json: async () => ({ ...LIST, orders: many, total: 30 }) } : inner(url, init),
  );
  renderPage();
  expect(await screen.findByText("1–25 of 30")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "‹ Previous" })).toBeDisabled();
  expect(screen.getByText("CO-270224-OEMA")).toBeInTheDocument();
  expect(screen.queryByText("CO-270225-OEMA")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Next ›" }));
  expect(screen.getByText("26–30 of 30")).toBeInTheDocument();
  expect(screen.getByText("Page 2 of 2")).toBeInTheDocument();
  expect(screen.getByText("CO-270225-OEMA")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Next ›" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "Received" } });
  expect(await screen.findByText("1–25 of 30")).toBeInTheDocument();
});

it("opens the detail with traceability, and blocks cancelling an order in production", async () => {
  mockApi();
  renderPage();
  fireEvent.click((await screen.findAllByRole("button", { name: "Detail" }))[0]);
  const dialog = await screen.findByRole("dialog", { name: "Order CO-270201-OEMA" });
  expect(within(dialog).getByText("PP-2027-02")).toBeInTheDocument();
  expect(within(dialog).getByRole("columnheader", { name: "Colour" })).toBeInTheDocument();
  expect(within(dialog).getByText("C07")).toBeInTheDocument();
  expect(within(dialog).getByText("WO-270201-IMM-04-FB-ARKA")).toBeInTheDocument();
  expect(within(dialog).getByText(/400 pcs from finished-goods stock/)).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Cancel order" })).toBeDisabled();
  expect(within(dialog).getByText(/already in production/)).toBeInTheDocument();
  expect(within(dialog).getByText(/No documents yet/)).toBeInTheDocument();
});

it("walks the create wizard: checks each step, reviews with server warnings, creates", async () => {
  mockApi();
  renderPage("/orders?add=1");
  const dialog = await screen.findByRole("dialog", { name: "Create customer order" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Next" }));
  expect(within(dialog).getByText("Choose a customer.")).toBeInTheDocument();
  fireEvent.change(within(dialog).getByLabelText("Customer"), { target: { value: "CUS-EXP" } });
  expect(within(dialog).getByLabelText("Customer PIC")).toHaveValue("Export desk");
  expect(within(dialog).getByLabelText("Order date")).toHaveValue("2027-02-01");
  fireEvent.change(within(dialog).getByLabelText("Reference"), { target: { value: "SET-PO-1" } });
  fireEvent.change(within(dialog).getByLabelText("Requested delivery date"), { target: { value: "2027-02-12" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Next" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "Next" }));
  expect(within(dialog).getByText("Add at least one product.")).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "+ Add product" }));
  fireEvent.change(within(dialog).getByLabelText("Product"), { target: { value: "FB-BROMO" } });
  fireEvent.change(within(dialog).getByLabelText("Quantity"), { target: { value: "300" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Add to order" }));
  expect(within(dialog).getByText("Choose a colour.")).toBeInTheDocument();
  expect(within(dialog).getAllByRole("option", { name: /^C0/ }).map((o) => o.textContent)).toEqual(["C01", "C02", "C04"]);
  fireEvent.change(within(dialog).getByLabelText("Colour"), { target: { value: "C02" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Add to order" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "+ Add product" }));
  fireEvent.change(within(dialog).getByLabelText("Product"), { target: { value: "FB-BROMO" } });
  fireEvent.change(within(dialog).getByLabelText("Colour"), { target: { value: "C02" } });
  fireEvent.change(within(dialog).getByLabelText("Quantity"), { target: { value: "5" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Add to order" }));
  expect(within(dialog).getByText(/already on the order/)).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "Next" }));
  expect(await within(dialog).findByText("Reference SET-PO-1 is already on order CO-1.")).toBeInTheDocument();
  expect(within(dialog).getByText(/can be delivered on the requested date/)).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Create order" }));
  expect(await within(dialog).findByText("CO-270212-EXP-AB12")).toBeInTheDocument();
  await waitFor(() => expect(calls.some((c) => c.url.endsWith("/api/orders") && c.method === "POST")).toBe(true));
  const sent = calls.find((c) => c.url.endsWith("/api/orders") && c.method === "POST")?.body as { lines: unknown[]; customer_id: string };
  expect(sent.customer_id).toBe("CUS-EXP");
  expect(sent.lines).toEqual([{ sku: "FB-BROMO", color: "C02", qty: 300, requested_delivery_date: null }]);
});

it("reads the demo PO in Replay and offers the change for approval", async () => {
  mockApi();
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: /Read the OEM-A revised schedule \(Replay\)/ }));
  expect(await screen.findByText("+40.0%")).toBeInTheDocument();
  expect(screen.getByText("matches 19,104")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Send 1 order change for approval" })).toBeEnabled();
  expect(screen.getByLabelText("Upload a customer PO (PDF)")).toBeDisabled();
});
