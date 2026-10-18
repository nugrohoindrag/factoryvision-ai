import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { AppProvider } from "../lib/store";
import { ProductionBoardPage } from "./ProductionBoard";
import { WorkOrdersPage } from "./WorkOrders";

const wo = (extra = {}) => ({
  wo_id: "WO-270201-IMM-04-FB-ARKA",
  plan_id: "p1",
  plan_number: "PP-2027-02",
  plan_status: "Confirmed",
  executing: true,
  line_id: "L1",
  sku: "FB-ARKA",
  product_name: "Front bumper ARKA",
  process: "Molding",
  routing_step: 1,
  predecessor_wo_id: null,
  parent_wo_id: null,
  children: [],
  qty: 1000,
  output_qty: 250,
  reject_qty: 4,
  scrap_qty: 0,
  progress_pct: 25,
  machine_id: "IMM-04",
  mold_id: "MLD-FB-ARKA",
  compatible_machines: ["IMM-04", "IMM-05"],
  planned_start: "2027-02-01T07:00+07:00",
  planned_end: "2027-02-01T19:00+07:00",
  actual_start: "2027-02-01T07:00+07:00",
  actual_end: null,
  shift: "A",
  priority: 1,
  status: "In Production",
  board_status: "At Risk",
  available_qty: null,
  cancel_reason: null,
  ...extra,
});

const LIST = {
  as_of: "2027-02-01",
  now: "2027-02-01T13:00+07:00",
  total: 3,
  counts: { Draft: 0, Confirmed: 1, Released: 1, "In Production": 1, Completed: 0, Cancelled: 0 },
  statuses: ["Draft", "Confirmed", "Released", "In Production", "Completed", "Cancelled"],
  processes: ["Molding", "Painting", "Assembly"],
  plans: [{ plan_id: "p1", plan_number: "PP-2027-02", status: "In Execution" }],
  work_orders: [wo(), wo({ wo_id: "WO-270201-PNT-01-FB-ARKA", process: "Painting", routing_step: 2, status: "Released", output_qty: 0, reject_qty: 0, progress_pct: 0 })],
  source: "work orders of this session's production plans",
};

const DETAIL = {
  ...wo({ wo_id: "WO-270201-PNT-01-FB-ARKA", process: "Painting", routing_step: 2, status: "Released", board_status: "Released", predecessor_wo_id: "WO-270201-IMM-04-FB-ARKA", available_qty: 0, machine_id: "PNT-01", mold_id: null, compatible_machines: ["PNT-01"], output_qty: 0 }),
  history: [{ at: "2027-02-01T07:05:00+00:00", by: "demo-manager", action: "Release to shop floor", detail: "Confirmed -> Released" }],
  predecessors: [{ wo_id: "WO-270201-IMM-04-FB-ARKA", process: "Molding", status: "In Production", output_qty: 0 }],
  successors: [{ wo_id: "WO-270201-ASM-01-FB-ARKA", process: "Assembly", status: "Confirmed" }],
  children_detail: [],
  demand: [{ order_number: "CO-270201-OEMA", customer_name: "Nusantara Motor (OEM-A)", sku: "FB-ARKA", qty: 1000, requested_delivery_date: "2027-02-04" }],
  transitions: [
    { to: "In Production", label: "Start", allowed: false, reasons: ["The previous process has handed over nothing yet (available 0)."] },
    { to: "Confirmed", label: "Recall from shop floor", allowed: true, reasons: [] },
    { to: "Cancelled", label: "Cancel", allowed: true, reasons: [] },
  ],
  can_split: false,
  dispatchable: true,
  max_priority: 5,
};

const item = (extra = {}) => ({
  id: "WO-A",
  kind: "WORK_ORDER",
  label: "WO-A",
  wo_id: "WO-A",
  plan_id: "p1",
  plan_number: "PP-2027-02",
  executing: true,
  sku: "FB-ARKA",
  process: "Painting",
  machine_id: "PNT-01",
  planned_start: "2027-02-01T09:00+07:00",
  planned_end: "2027-02-01T13:00+07:00",
  qty: 800,
  output_qty: 0,
  progress_pct: 0,
  status: "Confirmed",
  board_status: "Confirmed",
  conflicts: [] as unknown[],
  row: 0,
  ...extra,
});

const CLASH = { type: "MACHINE_CONFLICT", blocking: true, message: "WO-A (PP-2027-02) and WO-B (PP-2027-02-002) are booked on PNT-01 at the same time.", item_ids: ["WO-A", "WO-B"] };
const BOARD = {
  view: "machine",
  window_start: "2027-02-01T07:00+07:00",
  window_end: "2027-02-02T07:00+07:00",
  now: "2027-02-01T08:00+07:00",
  days: 1,
  lanes: [
    { id: "IMM-04", name: "IMM-04", subtitle: "Injection molding 3,500 t #4", rows: 1, items: [] },
    { id: "PNT-01", name: "PNT-01", subtitle: "Primer booth", rows: 2, items: [item({ conflicts: [CLASH] }), item({ id: "WO-B", label: "WO-B", wo_id: "WO-B", plan_number: "PP-2027-02-002", row: 1, conflicts: [CLASH] })] },
  ],
  conflicts: [CLASH, { type: "DELIVERY_RISK", blocking: false, message: "WO-B ends 90 min after its delivery date allows (scheduler).", item_ids: ["WO-B"] }],
  summary: { work_orders: 2, running: 0, late_or_at_risk: 0, blocking: 1, warnings: 1 },
  source: "work orders of this session's production plans",
};

const calls: { url: string; method: string; body?: unknown }[] = [];
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
      else if (url.startsWith("/api/work-orders/") && method === "GET") body = { ...DETAIL, wo_id: decodeURIComponent(url.split("/")[3]) };
      else if (url.startsWith("/api/work-orders")) body = method === "GET" ? LIST : DETAIL;
      else if (url.startsWith("/api/production-board/dispatch")) body = DETAIL;
      else if (url.startsWith("/api/production-board")) body = BOARD;
    }
    return { ok: true, json: async () => body };
  });
}

function renderPage(page: ReactElement) {
  render(
    <MemoryRouter>
      <AppProvider>{page}</AppProvider>
    </MemoryRouter>,
  );
}

it("lists work orders with progress, reject, status and KPIs, and filters on the server", async () => {
  mockApi();
  renderPage(<WorkOrdersPage />);
  expect(await screen.findByText("Work orders (2)")).toBeInTheDocument();
  const rows = screen.getAllByRole("row").slice(1);
  expect(within(rows[0]).getByText("250 / 1,000")).toBeInTheDocument();
  expect(within(rows[0]).getByText("4")).toHaveClass("risk");
  expect(within(rows[1]).getByText("Released")).toBeInTheDocument();
  expect(screen.getByText("Confirmed & Released").parentElement).toHaveTextContent("2");
  fireEvent.click(screen.getByRole("button", { name: "In Production" }));
  await waitFor(() => expect(calls.some((c) => c.url === "/api/work-orders?status=In+Production")).toBe(true));
  fireEvent.change(screen.getByLabelText("Search work orders"), { target: { value: "IMM-04" } });
  await waitFor(() => expect(calls.some((c) => c.url.includes("q=IMM-04"))).toBe(true));
});

it("opens a work order with its chain, served orders, guards and history, and moves it", async () => {
  mockApi();
  renderPage(<WorkOrdersPage />);
  fireEvent.click(await screen.findByRole("button", { name: "WO-270201-PNT-01-FB-ARKA" }));
  const dialog = await screen.findByRole("dialog", { name: "Work order WO-270201-PNT-01-FB-ARKA" });
  expect(await within(dialog).findByText(/CO-270201-OEMA Nusantara Motor/)).toBeInTheDocument();
  expect(within(dialog).getByText(/first process|WO-270201-IMM-04-FB-ARKA \(Molding/)).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Start" })).toBeDisabled();
  expect(within(dialog).getByText(/handed over nothing yet/)).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Cancel work order" })).toBeDisabled();
  fireEvent.click(within(dialog).getByRole("button", { name: "Recall from shop floor" }));
  await waitFor(() => expect(calls.find((c) => c.url.endsWith("/transition"))?.body).toEqual({ to: "Confirmed", reason: "" }));
  fireEvent.change(within(dialog).getByLabelText("Cancel reason"), { target: { value: "OEM moved the call-off" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel work order" }));
  await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/transition")).at(-1)?.body).toEqual({ to: "Cancelled", reason: "OEM moved the call-off" }));
  expect(within(dialog).getByText("History (1)")).toBeInTheDocument();
});

it("shows the guard reasons the server returns when a move is refused", async () => {
  mockApi((url, method) =>
    url.endsWith("/transition") && method === "POST"
      ? { ok: false, statusText: "Conflict", json: async () => ({ error: { code: "FV-PP-019", title: "Work order transition not allowed", message: "x", detail: { reasons: ["IMM-04 is down (Downtime); start once it runs again."] } } }) }
      : undefined,
  );
  renderPage(<WorkOrdersPage />);
  fireEvent.click(await screen.findByRole("button", { name: "WO-270201-PNT-01-FB-ARKA" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(await within(dialog).findByRole("button", { name: "Recall from shop floor" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("IMM-04 is down");
});

it("draws the board with stacked clashing bars, conflicts first, and dispatches from a bar", async () => {
  mockApi();
  renderPage(<ProductionBoardPage />);
  expect(await screen.findByText("Schedule conflicts")).toBeInTheDocument();
  const items = screen.getAllByRole("listitem");
  expect(items[0]).toHaveTextContent("Blocking");
  expect(items[1]).toHaveTextContent("Warning");
  expect(screen.getByText("IMM-04")).toBeInTheDocument(); // an idle machine keeps its lane
  const a = screen.getByRole("button", { name: "WO-A, Confirmed, blocking conflict" });
  const b = screen.getByRole("button", { name: "WO-B, Confirmed, blocking conflict" });
  expect(a).toHaveTextContent("FB-ARKA · PP-2027-02");
  expect(b).toHaveTextContent("FB-ARKA · PP-2027-02-002"); // two plans' jobs for one SKU tell apart
  expect(a).toHaveClass("clash");
  expect(a.style.top).not.toBe(b.style.top);
  expect(a.style.left).toBe(`${(100 * 2) / 24}%`);
  fireEvent.click(screen.getByRole("button", { name: "3 days" }));
  await waitFor(() => expect(calls.some((c) => c.url === "/api/production-board?view=machine&days=3")).toBe(true));
  fireEvent.click(a);
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(await within(dialog).findByLabelText("New start"), { target: { value: "2027-02-03T07:00" } });
  fireEvent.change(within(dialog).getByLabelText("New end"), { target: { value: "2027-02-03T11:00" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Reschedule" }));
  await waitFor(() =>
    expect(calls.find((c) => c.url === "/api/production-board/dispatch")?.body).toMatchObject({
      action: "RESCHEDULE",
      planned_start: "2027-02-03T07:00:00+07:00",
      planned_end: "2027-02-03T11:00:00+07:00",
    }),
  );
});

it("shows the empty board state with a way to the plans", async () => {
  mockApi((url) => (url.startsWith("/api/production-board") ? { ...BOARD, lanes: [], conflicts: [], summary: { work_orders: 0, running: 0, late_or_at_risk: 0, blocking: 0, warnings: 0 } } : undefined));
  renderPage(<ProductionBoardPage />);
  expect(await screen.findByText("Nothing scheduled in this window")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Production plans" })).toHaveAttribute("href", "/planning");
});
