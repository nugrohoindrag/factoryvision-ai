import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { formatIdr, formatIdrCompact } from "../lib/format";
import { AppProvider } from "../lib/store";
import { PurchasingPage } from "./Purchasing";

const SUMMARY = {
  scorecard: {
    source: "fv.fact_purchase_order, fv.dim_vendor",
    vendors: [{ vendor_id: "VEN-ANDALAS", vendor_name: "PT Andalas Polimindo", category: "Resin", po_count: 12, on_time_pct: 25, avg_delay_days: 4, quoted_lead_time_days: 7, effective_lead_time_days: 11 }],
  },
  resin_po: {
    need_by: "2027-02-12",
    options: [
      { vendor_id: "VEN-MERAPI", vendor_name: "PT Merapi Resin Indonesia", expected_arrival: "2027-02-08", expected_delay_days: 1, arrives_before_need: true, po_value_idr: 330000000 },
      { vendor_id: "VEN-ANDALAS", vendor_name: "PT Andalas Polimindo", expected_arrival: "2027-02-14", expected_delay_days: 4, arrives_before_need: false, po_value_idr: 315000000 },
    ],
    recommended_vendor: "PT Merapi Resin Indonesia",
    premium_vs_cheapest_idr: 15000000,
  },
  placed: [],
};
const po = (extra: Record<string, unknown> = {}) => ({
  po_id: "PO-2702-S-001",
  origin: "session",
  vendor_id: "VEN-MERAPI",
  vendor_name: "PT Merapi Resin Indonesia",
  status: "Issued",
  order_date: "2027-02-01",
  promised_date: "2027-02-08",
  expected_date: "2027-02-09",
  pr_id: "PR-2702-001",
  note: null,
  currency: "IDR",
  total_value_idr: 33000000,
  received_pct: 40,
  overdue: false,
  days_late: 0,
  lines: [{ line_id: "L1", material_id: "PP-EPDM-T20", material_name: "Resin", unit: "kg", qty: 1000, unit_price_idr: 33000, value_idr: 33000000, received_qty: 400, open_qty: 600 }],
  receipts: [{ gr_id: "GR-2702-S-001", po_id: "PO-2702-S-001", received_date: "2027-02-05", lot_id: "LOT-PPT20-S-0001", note: null, by: "user", lines: [{ material_id: "PP-EPDM-T20", qty: 400 }] }],
  history: [{ at: "2027-02-01T08:00:00Z", by: "user", action: "issue", from: "Draft", to: "Issued", reason: null }],
  ...extra,
});
const SEED = po({ po_id: "PO-2702-030", origin: "seed", vendor_id: "VEN-ANDALAS", vendor_name: "PT Andalas Polimindo", pr_id: null, overdue: true, days_late: 3, received_pct: 0, receipts: [], history: [] });
const pr = (extra: Record<string, unknown> = {}) => ({
  pr_id: "PR-2702-001",
  status: "Draft",
  created_at: "2027-02-01T07:00:00Z",
  created_by: "user",
  source: "mrp:MRP-2027-02-001",
  note: null,
  lines: [
    { line_id: "L1", material_id: "PP-EPDM-T20", material_name: "Resin", unit: "kg", qty: 1000, need_by: "2027-02-12", suggested_vendor_id: "VEN-MERAPI", suggested_vendor_name: "PT Merapi Resin Indonesia", source: "mrp:MRP-2027-02-001", duplicate_of: null },
    { line_id: "L2", material_id: "B-C07", material_name: "Basecoat C07", unit: "kg", qty: 50, need_by: "2027-02-10", suggested_vendor_id: "VEN-PAINT", suggested_vendor_name: "PT Warna", source: "mrp:MRP-2027-02-001", duplicate_of: "PR-2701-004" },
  ],
  po_ids: [],
  history: [{ at: "2027-02-01T07:00:00Z", by: "user", action: "create", from: null, to: "Draft", reason: null }],
  ...extra,
});
const VENDORS = {
  vendors: [
    { vendor_id: "VEN-ANDALAS", vendor_name: "PT Andalas Polimindo", category: "Resin", lead_time_days: 7, unit_price_idr: 31500, materials: ["PP-EPDM-T20"], on_time_pct: 25, avg_delay_days: 4, effective_lead_time_days: 11, po_count: 12 },
    { vendor_id: "VEN-MERAPI", vendor_name: "PT Merapi Resin Indonesia", category: "Resin", lead_time_days: 5, unit_price_idr: 33000, materials: [{ material_id: "PP-EPDM-T20", material_name: "Resin", unit: "kg" }], on_time_pct: 92, avg_delay_days: 0.5, effective_lead_time_days: 5.5, po_count: 8 },
    { vendor_id: "VEN-PAINT", vendor_name: "PT Warna", category: "Paint", lead_time_days: 10, unit_price_idr: 0, materials: ["B-C07"], on_time_pct: null, avg_delay_days: null, effective_lead_time_days: null, po_count: 0 },
  ],
};
const REC = {
  material_id: "PP-EPDM-T20",
  qty: 1000,
  need_by: "2027-02-12",
  options: [
    { vendor_id: "VEN-MERAPI", vendor_name: "PT Merapi Resin Indonesia", expected_arrival: "2027-02-08", arrives_before_need: true, unit_price_idr: 33000, po_value_idr: 33000000, avg_delay_days: 0.5, expected_delay_days: 0.5 },
    { vendor_id: "VEN-ANDALAS", vendor_name: "PT Andalas Polimindo", expected_arrival: "2027-02-14", arrives_before_need: false, unit_price_idr: 31500, po_value_idr: 31500000, avg_delay_days: 4, expected_delay_days: 4 },
  ],
  recommended_vendor: "PT Merapi Resin Indonesia",
  recommended_vendor_id: "VEN-MERAPI",
  premium_vs_cheapest_idr: 1500000,
};
const MATERIALS = { materials: [{ material_id: "PP-EPDM-T20", material_name: "Resin", unit: "kg" }, { material_id: "B-C07", material_name: "Basecoat C07", unit: "kg" }], groups: [] };
const DOC = "PURCHASE ORDER PO-2702-S-001\nVendor: PT Merapi Resin Indonesia";

const calls: { url: string; method: string; body?: unknown }[] = [];
const fail = (code: string, title: string, message: string, status = 409) => ({ ok: false, status, statusText: "Conflict", json: async () => ({ error: { code, title, message } }) });

function mockApi(over: (url: string, method: string) => unknown = () => undefined) {
  calls.length = 0;
  globalThis.fetch = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
    const o = over(url, method);
    if (o && typeof o === "object" && "ok" in (o as object)) return o;
    let body: unknown = o ?? { pending: 0, actions: [] };
    if (o === undefined) {
      const P = "/api/purchasing";
      if (url.startsWith("/api/mode")) body = { ai_enabled: false };
      else if (url.startsWith("/api/modules/purchasing/summary")) body = SUMMARY;
      else if (url.startsWith(`${P}/recommend`)) body = REC;
      else if (url === `${P}/vendors`) body = VENDORS;
      else if (url.startsWith(`${P}/vendors/`)) body = { ...VENDORS.vendors[1], scorecard: { on_time_pct: 92, po_count: 8 }, open_pos: [po()], received_pos: [po({ po_id: "PO-2701-S-009", status: "Received", received_pct: 100 })] };
      else if (url.endsWith("/document")) return { ok: true, json: async () => ({}), blob: async () => ({ text: async () => DOC }) };
      else if (url.endsWith("/convert")) body = { po_ids: ["PO-2702-S-002", "PO-2702-S-003"] };
      else if (url.startsWith(`${P}/requisitions/`) && method === "POST") body = pr({ status: "Submitted" });
      else if (url.startsWith(`${P}/requisitions/`)) body = pr();
      else if (url === `${P}/requisitions` && method === "POST") body = pr({ pr_id: "PR-2702-002" });
      else if (url.startsWith(`${P}/requisitions`)) body = { requisitions: [pr(), pr({ pr_id: "PR-2702-000", status: "Approved", lines: [], po_ids: ["PO-2702-S-001"] })] };
      else if (url.endsWith("/receipts") && method === "POST") body = { gr_id: "GR-2702-S-002", po_id: "PO-2702-S-001", received_date: "2027-02-06", lot_id: "LOT-PPT20-S-0002", note: null, by: "user", lines: [{ material_id: "PP-EPDM-T20", qty: 600 }] };
      else if (url.startsWith(`${P}/orders/PO-2702-030`)) body = SEED;
      else if (url.startsWith(`${P}/orders/`) && method === "POST") body = po();
      else if (url.startsWith(`${P}/orders/`)) body = po();
      else if (url.startsWith(`${P}/orders`)) body = { orders: [po(), SEED] };
      else if (url.startsWith(`${P}/receipts`)) body = { receipts: po().receipts };
      else if (url.startsWith("/api/master/materials")) body = MATERIALS;
      else if (url.startsWith("/api/inventory")) body = { rows: [{ material_id: "PP-EPDM-T20", unit: "kg", warehouse_id: "WH-RES", on_hand_qty: 12500 }, { material_id: "PP-EPDM-T20", unit: "kg", warehouse_id: "WH-2", on_hand_qty: 500 }] };
    }
    return { ok: true, json: async () => body };
  });
}

function renderPage(path = "/purchasing") {
  render(
    <MemoryRouter initialEntries={[path]}>
      <AppProvider>
        <PurchasingPage />
      </AppProvider>
    </MemoryRouter>,
  );
}

it("overview: keeps the scorecard and the urgent resin PO, adds KPIs, overdue POs and pending PRs", async () => {
  mockApi((url) =>
    url.startsWith("/api/modules/purchasing/summary")
      ? { ...SUMMARY, kpis: { open_pos: 4, overdue_pos: 1, open_value_idr: 125000000, pending_requisitions: 2 }, overdue: [SEED], pending_requisitions: [pr({ status: "Submitted" })] }
      : undefined,
  );
  renderPage();
  expect(await screen.findByRole("heading", { name: "Vendor scorecard (90 days)" })).toBeInTheDocument();
  expect(screen.getByText("25.0%")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Urgent resin PO: need by 2027-02-12" })).toBeInTheDocument();
  expect(screen.getByText("PT Merapi Resin Indonesia", { selector: "strong" })).toBeInTheDocument();
  expect(screen.getByText(formatIdr(330000000))).toBeInTheDocument();
  expect(screen.getByText("Open POs").nextSibling).toHaveTextContent("4");
  expect(screen.getByText(formatIdrCompact(125000000))).toBeInTheDocument();
  expect(screen.getByText("PRs waiting for approval")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Overdue purchase orders (1)" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Requisitions waiting for approval (1)" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Open PO-2702-030" }));
  expect(await screen.findByText(/Seed PO from the plant's history/)).toBeInTheDocument();
});

it("overview: a summary without the new keys still renders today's cards", async () => {
  mockApi();
  renderPage();
  expect(await screen.findByRole("heading", { name: "Vendor scorecard (90 days)" })).toBeInTheDocument();
  expect(screen.queryByText("Open POs")).not.toBeInTheDocument();
  expect(screen.queryByText(/Overdue purchase orders/)).not.toBeInTheDocument();
  expect(screen.getByPlaceholderText("Which vendor should supply the next PP-EPDM-T20 lot, and why?")).toBeInTheDocument();
});

it("requisitions: list with status filter, then a manual PR with a live suggested vendor", async () => {
  mockApi();
  renderPage("/purchasing?tab=requisitions");
  expect(await screen.findByText("PR-2702-001")).toBeInTheDocument();
  expect(screen.getByText("duplicate")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Filter requisitions by status"), { target: { value: "Submitted" } });
  await waitFor(() => expect(calls.some((c) => c.url === "/api/purchasing/requisitions?status=Submitted")).toBe(true));

  fireEvent.click(screen.getByRole("button", { name: "+ New requisition" }));
  const dlg = screen.getByRole("dialog", { name: "New purchase requisition" });
  const create = within(dlg).getByRole("button", { name: "Create requisition" });
  expect(create).toBeDisabled();
  await within(dlg).findByRole("option", { name: "PP-EPDM-T20 · Resin" });
  fireEvent.change(within(dlg).getByLabelText("Material of line 1"), { target: { value: "PP-EPDM-T20" } });
  fireEvent.change(within(dlg).getByLabelText("Qty of line 1"), { target: { value: "-5" } });
  expect(within(dlg).getByText("Qty must be above 0.")).toBeInTheDocument();
  fireEvent.change(within(dlg).getByLabelText("Qty of line 1"), { target: { value: "1000" } });
  fireEvent.change(within(dlg).getByLabelText("Need by of line 1"), { target: { value: "2027-02-12" } });
  expect(await within(dlg).findByTestId("suggested-vendor")).toHaveTextContent("Suggested: PT Merapi Resin Indonesia · arrives 2027-02-08 in time");
  expect(calls.some((c) => c.url === "/api/purchasing/recommend?material_id=PP-EPDM-T20&qty=1000&need_by=2027-02-12")).toBe(true);
  fireEvent.change(within(dlg).getByLabelText("Requisition note"), { target: { value: "urgent" } });
  fireEvent.click(create);
  await waitFor(() => expect(calls.find((c) => c.url === "/api/purchasing/requisitions" && c.method === "POST")?.body).toEqual({ lines: [{ material_id: "PP-EPDM-T20", qty: 1000, need_by: "2027-02-12" }], note: "urgent" }));
  await waitFor(() => expect(calls.some((c) => c.url === "/api/purchasing/requisitions/PR-2702-002")).toBe(true));
});

it("requisition detail: lines with duplicate flag, history, Submit and Cancel on a Draft", async () => {
  mockApi();
  renderPage("/purchasing?tab=requisitions&pr=PR-2702-001");
  expect(await screen.findByText("Duplicate of PR-2701-004")).toBeInTheDocument();
  expect(screen.getByText("– → Draft")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Submit" }));
  await waitFor(() => expect(calls.some((c) => c.url === "/api/purchasing/requisitions/PR-2702-001/submit" && c.method === "POST")).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(calls.some((c) => c.url === "/api/purchasing/requisitions/PR-2702-001/cancel")).toBe(true));
});

it("requisition detail: Approve, Reject needs a reason, and a wrong transition shows the server error", async () => {
  mockApi((url, method) => {
    if (url === "/api/purchasing/requisitions/PR-2702-001" && method === "GET") return pr({ status: "Submitted" });
    if (url.endsWith("/approve")) return fail("FV-PUR-005", "Invalid status change", "The PR is Approved already.");
    return undefined;
  });
  renderPage("/purchasing?tab=requisitions&pr=PR-2702-001");
  fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Invalid status change (FV-PUR-005): The PR is Approved already.");
  fireEvent.click(screen.getByRole("button", { name: "Reject" }));
  const dlg = screen.getByRole("dialog", { name: "Reject PR-2702-001" });
  expect(within(dlg).getByRole("button", { name: "Reject requisition" })).toBeDisabled();
  fireEvent.change(within(dlg).getByLabelText("Reject reason"), { target: { value: "Stock is enough" } });
  fireEvent.click(within(dlg).getByRole("button", { name: "Reject requisition" }));
  await waitFor(() => expect(calls.find((c) => c.url.endsWith("/reject"))?.body).toEqual({ reason: "Stock is enough" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Reject PR-2702-001" })).not.toBeInTheDocument());
});

it("requisition detail: Convert to PO with a vendor override returns one PO per vendor", async () => {
  mockApi((url, method) => (url === "/api/purchasing/requisitions/PR-2702-001" && method === "GET" ? pr({ status: "Approved" }) : undefined));
  renderPage("/purchasing?tab=requisitions&pr=PR-2702-001");
  fireEvent.click(await screen.findByRole("button", { name: "Convert to PO" }));
  const dlg = screen.getByRole("dialog", { name: "Convert PR-2702-001 to PO" });
  await within(dlg).findByRole("option", { name: "PT Andalas Polimindo" });
  fireEvent.change(within(dlg).getByLabelText("Vendor for PP-EPDM-T20"), { target: { value: "VEN-ANDALAS" } });
  fireEvent.click(within(dlg).getByRole("button", { name: "Create purchase orders" }));
  expect(await within(dlg).findByText(/Created 2 purchase order\(s\): PO-2702-S-002, PO-2702-S-003/)).toBeInTheDocument();
  expect(calls.find((c) => c.url.endsWith("/convert"))?.body).toEqual({ vendor_overrides: { "PP-EPDM-T20": "VEN-ANDALAS" } });
  fireEvent.click(within(dlg).getByRole("button", { name: "Open PO-2702-S-002" }));
  await waitFor(() => expect(calls.some((c) => c.url === "/api/purchasing/orders/PO-2702-S-002")).toBe(true));
});

it("orders: list with seed badge, overdue, received bar and filters", async () => {
  mockApi();
  renderPage("/purchasing?tab=orders");
  expect(await screen.findByText("PO-2702-030")).toBeInTheDocument();
  expect(screen.getByText("seed")).toBeInTheDocument();
  expect(screen.getByText("overdue 3 d")).toBeInTheDocument();
  expect(screen.getByLabelText("Received 40%")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Filter orders by status"), { target: { value: "Issued" } });
  await screen.findByRole("option", { name: "PT Warna" });
  fireEvent.change(screen.getByLabelText("Filter orders by vendor"), { target: { value: "VEN-ANDALAS" } });
  fireEvent.click(screen.getByLabelText("Only overdue"));
  await waitFor(() => expect(calls.some((c) => c.url === "/api/purchasing/orders?status=Issued&vendor_id=VEN-ANDALAS&overdue=true")).toBe(true));
  expect(screen.queryByText("PO-2702-S-001")).not.toBeInTheDocument();
});

it("order detail: lines, receipts, history, document; Expedite only to an earlier date", async () => {
  mockApi();
  renderPage("/purchasing?tab=orders&po=PO-2702-S-001");
  expect(await screen.findByText("GR-2702-S-001")).toBeInTheDocument();
  expect(screen.getByText("LOT-PPT20-S-0001")).toBeInTheDocument();
  expect(screen.getByText("Draft → Issued")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Issue" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "PO document" }));
  expect(await screen.findByLabelText("PO document text")).toHaveTextContent("PURCHASE ORDER PO-2702-S-001");
  fireEvent.click(screen.getByRole("button", { name: "Close" }));

  fireEvent.click(screen.getByRole("button", { name: "Expedite" }));
  const dlg = screen.getByRole("dialog", { name: "Expedite PO-2702-S-001" });
  fireEvent.change(within(dlg).getByLabelText("New promised date"), { target: { value: "2027-02-10" } });
  expect(within(dlg).getByText("The new date must be before 2027-02-08.")).toBeInTheDocument();
  fireEvent.change(within(dlg).getByLabelText("New promised date"), { target: { value: "2027-02-06" } });
  fireEvent.change(within(dlg).getByLabelText("Expedite reason"), { target: { value: "Line stops on 7 Feb" } });
  fireEvent.click(within(dlg).getByRole("button", { name: "Expedite" }));
  await waitFor(() => expect(calls.find((c) => c.url.endsWith("/expedite"))?.body).toEqual({ new_promised_date: "2027-02-06", reason: "Line stops on 7 Feb" }));
});

it("order detail: a Draft can be issued or cancelled", async () => {
  mockApi((url, method) => (url === "/api/purchasing/orders/PO-2702-S-001" && method === "GET" ? po({ status: "Draft", received_pct: 0, receipts: [], lines: [{ ...po().lines[0], received_qty: 0, open_qty: 1000 }] }) : undefined));
  renderPage("/purchasing?tab=orders&po=PO-2702-S-001");
  fireEvent.click(await screen.findByRole("button", { name: "Issue" }));
  await waitFor(() => expect(calls.some((c) => c.url === "/api/purchasing/orders/PO-2702-S-001/issue" && c.method === "POST")).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(calls.some((c) => c.url === "/api/purchasing/orders/PO-2702-S-001/cancel")).toBe(true));
  expect(screen.queryByRole("button", { name: "Receive" })).not.toBeInTheDocument();
});

it("receive: defaults to the open qty, enforces the 5% tolerance and toasts the new stock", async () => {
  mockApi();
  renderPage("/purchasing?tab=orders&po=PO-2702-S-001");
  fireEvent.click(await screen.findByRole("button", { name: "Receive" }));
  const dlg = screen.getByRole("dialog", { name: "Receive PO-2702-S-001" });
  const input = within(dlg).getByLabelText("Receive qty of PP-EPDM-T20");
  expect(input).toHaveValue(600);
  expect(within(dlg).getByText("630")).toBeInTheDocument();
  fireEvent.change(input, { target: { value: "631" } });
  expect(within(dlg).getByText("Above the 5% tolerance (max 630).")).toBeInTheDocument();
  expect(within(dlg).getByRole("button", { name: "Post goods receipt" })).toBeDisabled();
  fireEvent.change(input, { target: { value: "600" } });
  fireEvent.change(within(dlg).getByLabelText("Lot id"), { target: { value: "LOT-X" } });
  fireEvent.click(within(dlg).getByRole("button", { name: "Post goods receipt" }));
  expect(await screen.findByRole("status")).toHaveTextContent("GR-2702-S-002 recorded for PO-2702-S-001 · lot LOT-PPT20-S-0002. On hand now: PP-EPDM-T20 13,000 kg.");
  expect(calls.find((c) => c.url.endsWith("/receipts") && c.method === "POST")?.body).toEqual({ lines: [{ material_id: "PP-EPDM-T20", qty: 600 }], lot_id: "LOT-X" });
  expect(screen.queryByRole("dialog", { name: "Receive PO-2702-S-001" })).not.toBeInTheDocument();
});

it("receive: a seed PO is read-only but can be received; the server's refusal is shown", async () => {
  mockApi((url, method) => (url.endsWith("/receipts") && method === "POST" ? fail("FV-PUR-009", "Over-receipt", "600 kg is above the tolerance.", 422) : undefined));
  renderPage("/purchasing?tab=orders&po=PO-2702-030");
  expect(await screen.findByText(/Seed PO from the plant's history/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Expedite" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Close" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Receive" }));
  const dlg = screen.getByRole("dialog", { name: "Receive PO-2702-030" });
  fireEvent.click(within(dlg).getByRole("button", { name: "Post goods receipt" }));
  expect(await within(dlg).findByRole("alert")).toHaveTextContent("Over-receipt (FV-PUR-009): 600 kg is above the tolerance.");
});

it("receipts: lists goods receipts and filters by PO", async () => {
  mockApi();
  renderPage("/purchasing?tab=receipts");
  expect(await screen.findByText("GR-2702-S-001")).toBeInTheDocument();
  expect(screen.getByText("400 PP-EPDM-T20")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Filter receipts by PO"), { target: { value: "PO-2702-030" } });
  await waitFor(() => expect(calls.some((c) => c.url === "/api/purchasing/receipts?po_id=PO-2702-030")).toBe(true));
});

it("vendors: scorecard, price on quotation, detail and the recommend form", async () => {
  mockApi();
  renderPage("/purchasing?tab=vendors");
  expect(await screen.findByText("PT Warna")).toBeInTheDocument();
  expect(screen.getAllByText("price on quotation").length).toBeGreaterThan(0);
  expect(screen.getByText("92.0%")).toBeInTheDocument();
  await screen.findByRole("option", { name: "PP-EPDM-T20 · Resin" });
  fireEvent.change(screen.getByLabelText("Material to buy"), { target: { value: "PP-EPDM-T20" } });
  fireEvent.change(screen.getByLabelText("Qty to buy"), { target: { value: "1000" } });
  fireEvent.change(screen.getByLabelText("Needed by"), { target: { value: "2027-02-12" } });
  fireEvent.click(screen.getByRole("button", { name: "Recommend" }));
  expect(await screen.findByText("recommended")).toBeInTheDocument();
  expect(screen.getByText(/premium Rp1\.500\.000 vs the cheapest/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Details of PT Merapi Resin Indonesia" }));
  expect(await screen.findByRole("heading", { name: "Open purchase orders (1)" })).toBeInTheDocument();
  expect(screen.getByText("PO-2701-S-009")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Materials supplied" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "PO-2702-S-001" }));
  await waitFor(() => expect(calls.some((c) => c.url === "/api/purchasing/orders/PO-2702-S-001")).toBe(true));
});

it("an unknown requisition shows the catalog error", async () => {
  mockApi((url) => (url === "/api/purchasing/requisitions/PR-NOPE" ? fail("FV-PUR-006", "Unknown requisition", "PR-NOPE does not exist.", 404) : undefined));
  renderPage("/purchasing?tab=requisitions&pr=PR-NOPE");
  expect(await screen.findByRole("alert")).toHaveTextContent("Unknown requisition (FV-PUR-006)");
  fireEvent.click(screen.getByRole("button", { name: "← All requisitions" }));
  expect(await screen.findByText("PR-2702-001")).toBeInTheDocument();
});
