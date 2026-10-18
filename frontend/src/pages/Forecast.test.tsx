import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AppProvider } from "../lib/store";
import { ForecastPage, insufficientText, weeklyTotals } from "./Forecast";

const SKUS = ["FB-ARKA", "RB-CITRA"];
const summary = (extra = {}) => ({
  engine: "local trend+weekday (dev)",
  last7_daily_mean: 689.6,
  change_by_final_week_pct: 36.9,
  peak_week: 6,
  peak_change_pct: 36.9,
  forecast_total: 35332,
  history_total: 48145,
  history_weeks_with_orders: 13,
  history_weeks: 13,
  insufficient_history: false,
  ...extra,
});
const head = (id: string, status: string, extra = {}) => ({
  forecast_id: id,
  status,
  superseded_by: status === "Superseded" ? "FC-2027-02-002" : null,
  superseded_at: status === "Superseded" ? "2027-02-01T08:00:00+00:00" : null,
  supersedes: [],
  window_days: 90,
  horizon_weeks: 2,
  skus: SKUS,
  per_customer: false,
  engine: "local trend+weekday (dev)",
  method: "Linear trend + weekday pattern fitted on the last 42 days of the 90-day window.",
  rules: "Cancelled orders are excluded, days without orders count as zero, and today (2027-02-01) is not part of the history.",
  generated_at: "2027-02-01T07:00:00+00:00",
  generated_by: "seed",
  period_start: "2027-02-01",
  period_end: "2027-02-14",
  history_start: "2026-11-03",
  history_end: "2027-01-31",
  line_count: 4,
  insufficient_skus: ["RB-CITRA"],
  sku_summary: { "FB-ARKA": summary(), "RB-CITRA": summary({ change_by_final_week_pct: 0.7, insufficient_history: true, history_weeks_with_orders: 11 }) },
  calendar_notes: ["Rainy season Nov-Mar"],
  source: "fv.fact_customer_order",
  ...extra,
});
const line = (sku: string, week: number, qty: number, extra = {}) => ({
  forecast_line_id: `X-${sku}-${week}`,
  sku,
  customer_id: null,
  week,
  week_start: week === 1 ? "2027-02-01" : "2027-02-08",
  week_end: week === 1 ? "2027-02-07" : "2027-02-14",
  forecast_qty: qty,
  history_weeks_with_orders: 13,
  history_weeks: 13,
  insufficient_history: false,
  ...extra,
});
const DETAIL = {
  ...head("FC-2027-02-002", "Generated"),
  lines: [line("FB-ARKA", 1, 5169), line("FB-ARKA", 2, 5457), line("RB-CITRA", 1, 4000), line("RB-CITRA", 2, 4100)],
  history: {
    "FB-ARKA": [{ week_start: "2027-01-18", days: 7, qty: 0 }, { week_start: "2027-01-25", days: 7, qty: 4130 }],
    "RB-CITRA": [{ week_start: "2027-01-25", days: 7, qty: 3000 }],
  },
  used_by_plans: ["PP-2027-02"],
  used_by_plan_refs: [],
};
const LIST = {
  snapshots: [head("FC-2027-02-002", "Generated"), head("FC-2027-02-001", "Superseded")],
  statuses: ["Generated", "Superseded"],
  windows: [30, 60, 90],
  horizons: [4, 6, 8],
  products: [{ sku: "FB-ARKA", name: "Front bumper ARKA (MPV)" }, { sku: "RB-CITRA", name: "Rear bumper CITRA (Hatchback)" }],
  customers: [],
  rules: "Cancelled orders are excluded, days without orders count as zero, and today (2027-02-01) is not part of the history.",
};
const VS = {
  sku: "FB-ARKA",
  forecast_id: "FC-2027-02-002",
  customer_po_change_pct: 40,
  weeks: [{ week: 1, week_start: "2027-02-01", forecast_qty: 5169, firm_qty: 5584, schedule_qty: 5970, revised_schedule_qty: 8358, uncovered_qty: 0, firm_coverage_pct: 108 }],
  forecast_total: 5169,
  firm_total: 5584,
  note: "Firm = open customer orders.",
  source: "BQML forecast",
};
const CMP = { forecast_id: "FC-2027-02-002", rows: [{ sku: "FB-ARKA", forecast_qty: 10626, actual_qty: 5584, variance_qty: -5042, variance_pct: -47.4, insufficient_history: false }], weeks: [], note: "Actual = customer orders on the book." };
const ACC = { method: "Backtest of the local model.", rows: [{ sku: "FB-ARKA", mape_pct: 12.3, bias_pct: -8.1, forecast_total: 14000, actual_total: 15000 }] };

const calls: { url: string; method: string; body?: unknown }[] = [];
type Override = (url: string, method: string) => { status: number; body: unknown } | undefined;

function mockApi(override?: Override) {
  calls.length = 0;
  globalThis.fetch = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
    const o = override?.(url, method);
    if (o) return { ok: o.status < 400, status: o.status, statusText: "x", json: async () => o.body };
    let body: unknown = { pending: 0, actions: [] };
    if (url.includes("/api/mode")) body = { ai_enabled: false };
    else if (url.startsWith("/api/forecasts/accuracy")) body = ACC;
    else if (url.includes("/vs-orders")) body = { ...VS, sku: new URL(url, "http://x").searchParams.get("sku") };
    else if (url.includes("/comparison")) body = CMP;
    else if (url === "/api/forecasts" && method === "POST") body = { ...DETAIL, forecast_id: "FC-2027-02-003", supersedes: ["FC-2027-02-002"] };
    else if (url.startsWith("/api/forecasts/FC-2027-02-001")) body = { ...DETAIL, ...head("FC-2027-02-001", "Superseded"), used_by_plans: [] };
    else if (url.startsWith("/api/forecasts/FC-")) body = { ...DETAIL, forecast_id: url.split("/")[3].split("?")[0] };
    else if (url.startsWith("/api/forecasts")) body = url.includes("status=Superseded") ? { ...LIST, snapshots: [LIST.snapshots[1]] } : LIST;
    return { ok: true, status: 200, json: async () => body };
  });
}

function renderPage() {
  render(
    <MemoryRouter>
      <AppProvider>
        <ForecastPage />
      </AppProvider>
    </MemoryRouter>,
  );
}

it("sums per-customer lines per week and words the insufficient-history badge", () => {
  const lines = [line("FB-ARKA", 1, 10, { customer_id: "A" }), line("FB-ARKA", 1, 5, { customer_id: "B" }), line("FB-ARKA", 2, 7), line("RB-CITRA", 1, 99)];
  expect(weeklyTotals(lines, "FB-ARKA")).toEqual([
    { week: 1, week_start: "2027-02-01", qty: 15 },
    { week: 2, week_start: "2027-02-08", qty: 7 },
  ]);
  expect(insufficientText(11, 13)).toBe("Insufficient history: only 11 of 13 weeks have orders");
});

it("opens the newest Generated snapshot with rules, header, banner, KPIs and history table", async () => {
  mockApi();
  renderPage();
  expect(await screen.findByRole("heading", { name: "Demand Forecast" })).toBeInTheDocument();
  expect(await screen.findByText(/days without orders count as zero/)).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "Snapshot FC-2027-02-002" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /FC-2027-02-002 · Generated/ })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("status")).toHaveTextContent("Used by Production Plan: PP-2027-02. This snapshot will not change");
  expect(screen.getByText("+36.9%", { selector: ".kpi-value" })).toBeInTheDocument();
  expect(screen.getByText("1 SKU")).toBeInTheDocument();
  expect(await screen.findByText("+40.0%", { selector: ".kpi-value" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /FB-ARKA — Front bumper ARKA \(MPV\)/ })).toBeInTheDocument();
  const hist = screen.getByRole("table", { name: "Weekly order history" });
  expect(hist).toHaveTextContent("01-18");
  expect(hist).toHaveTextContent("0");
  expect(screen.getByText(/How it works: Linear trend/)).toBeInTheDocument();
  expect(await screen.findByText("8,358")).toBeInTheDocument();
  expect(await screen.findByText("12.3%")).toBeInTheDocument();
  expect(screen.getByText(/-5,042/)).toBeInTheDocument();
});

it("shows the insufficient-history badge for a thin SKU", async () => {
  mockApi();
  renderPage();
  fireEvent.change(await screen.findByLabelText("SKU"), { target: { value: "RB-CITRA" } });
  expect(await screen.findByText("Insufficient history: only 11 of 13 weeks have orders")).toBeInTheDocument();
  await waitFor(() => expect(calls.some((c) => c.url.includes("vs-orders?sku=RB-CITRA"))).toBe(true));
});

it("filters snapshots by status and shows a superseded snapshot as read-only history", async () => {
  mockApi();
  renderPage();
  await screen.findByRole("heading", { name: "Snapshot FC-2027-02-002" });
  fireEvent.change(screen.getByLabelText("Filter snapshots by status"), { target: { value: "Superseded" } });
  expect(await screen.findByRole("heading", { name: "Snapshot FC-2027-02-001" })).toBeInTheDocument();
  expect(calls.some((c) => c.url === "/api/forecasts?status=Superseded")).toBe(true);
  expect(await screen.findByText(/Superseded by/)).toHaveTextContent("Its numbers are kept exactly as they were generated.");
});

it("generates with the chosen window, horizon, SKU filter and per-customer flag", async () => {
  mockApi();
  renderPage();
  await screen.findByRole("heading", { name: "Snapshot FC-2027-02-002" });
  fireEvent.click(screen.getByRole("button", { name: "30 days" }));
  fireEvent.change(screen.getByLabelText("Horizon"), { target: { value: "4" } });
  fireEvent.click(screen.getByLabelText("Only FB-ARKA"));
  fireEvent.click(screen.getByLabelText("Break down per customer"));
  fireEvent.click(screen.getByRole("button", { name: "Generate forecast" }));
  expect(await screen.findByText(/FC-2027-02-003 generated with 4 lines\. FC-2027-02-002 is now Superseded/)).toBeInTheDocument();
  const post = calls.find((c) => c.method === "POST" && c.url === "/api/forecasts");
  expect(post?.body).toEqual({ window_days: 30, horizon_weeks: 4, skus: ["FB-ARKA"], per_customer: true });
  expect(await screen.findByRole("heading", { name: "Snapshot FC-2027-02-003" })).toBeInTheDocument();
});

it("shows field errors from the API next to the inputs", async () => {
  mockApi((url, method) =>
    url === "/api/forecasts" && method === "POST"
      ? { status: 400, body: { error: { code: "FV-FC-001", title: "Invalid forecast parameters", message: "The forecast parameters have fields to fix.", detail: { fields: [{ field: "horizon_weeks", message: "The horizon must be one of 4, 6 or 8 weeks; got 5." }] } } } }
      : undefined,
  );
  renderPage();
  await screen.findByRole("heading", { name: "Snapshot FC-2027-02-002" });
  fireEvent.click(screen.getByRole("button", { name: "Generate forecast" }));
  expect(await screen.findByText("The horizon must be one of 4, 6 or 8 weeks; got 5.")).toBeInTheDocument();
  expect(screen.getByText(/Could not generate the forecast: Invalid forecast parameters \(FV-FC-001\)/)).toBeInTheDocument();
});

it("shows an error with Retry, then recovers", async () => {
  let fail = true;
  mockApi((url) => (fail && url.startsWith("/api/forecasts") && !url.includes("FC-") && !url.includes("accuracy") ? { status: 503, body: { error: { code: "FV-DATA-002", title: "Warehouse unavailable", message: "down" } } } : undefined));
  renderPage();
  expect(await screen.findByText(/Could not load forecasts: Warehouse unavailable \(FV-DATA-002\)/)).toBeInTheDocument();
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("heading", { name: "Snapshot FC-2027-02-002" })).toBeInTheDocument();
});

it("shows the empty state with a Generate button when there is no snapshot", async () => {
  mockApi((url, method) => (url === "/api/forecasts" && method === "GET" ? { status: 200, body: { ...LIST, snapshots: [] } } : undefined));
  renderPage();
  expect(await screen.findByText("No forecast snapshot yet.")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Generate forecast" })).toHaveLength(2);
});
