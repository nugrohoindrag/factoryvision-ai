import { fireEvent, render, screen } from "@testing-library/react";
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
    status_counts: { Received: 40, Planned: 18, Completed: 240 },
    open_count: 58,
    open_qty: 23671,
    due_this_week: 58,
    on_time_delivery_pct: 97.9,
    shipments_30d: 240,
    open_by_customer: [{ customer_id: "CUS-OEMA", customer_name: "Nusantara Motor (OEM-A)", open_qty: 12000 }],
    open_by_week: [],
    open_orders: [
      { order_id: "SO-270201-001", customer_id: "CUS-OEMA", customer_name: "Nusantara Motor (OEM-A)", sku: "FB-ARKA", order_qty: 900, requested_delivery_date: "2027-02-01", order_status: "Planned" },
      { order_id: "SO-270203-004", customer_id: "CUS-OEMB", customer_name: "Garuda Auto (OEM-B)", sku: "FB-BROMO", order_qty: 450, requested_delivery_date: "2027-02-03", order_status: "Received" },
    ],
    source: "fv.fact_customer_order",
  },
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

function mockApi() {
  globalThis.fetch = vi.fn().mockImplementation(async (url: string) => {
    const body = url.includes("/api/orders/po/read") ? READ : url.includes("/api/modules/orders") ? SUMMARY : url.includes("/api/mode") ? { ai_enabled: false } : { pending: 0, actions: [] };
    return { ok: true, json: async () => body };
  });
}

function renderPage() {
  render(
    <MemoryRouter>
      <AppProvider>
        <OrdersPage />
      </AppProvider>
    </MemoryRouter>,
  );
}

it("shows the order book with status flow and filters open orders", async () => {
  mockApi();
  renderPage();
  expect(await screen.findByRole("heading", { name: "Customer Orders" })).toBeInTheDocument();
  expect(screen.getByText("97.9%")).toBeInTheDocument();
  expect(screen.getByText("Open orders (2)")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Filter by SKU"), { target: { value: "FB-BROMO" } });
  expect(screen.getByText("Open orders (1)")).toBeInTheDocument();
  expect(screen.queryByText("SO-270201-001")).not.toBeInTheDocument();
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
