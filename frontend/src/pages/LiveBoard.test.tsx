import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AppProvider } from "../lib/store";
import LiveBoardPage from "./LiveBoard";

const shift = (oee: number | null, perf: number | null) => ({ planned_min: 240, run_min: 228, shots: 202, good: 195, availability_pct: 95, performance_pct: perf, quality_pct: perf == null ? null : 96.5, oee_pct: oee });
const machine = (id: string, name: string, area: string, status: string, extra: Record<string, unknown> = {}) => ({
  machine_id: id,
  machine_name: name,
  process: area === "molding" ? "Injection Molding" : "Painting",
  area,
  status,
  reason_code: null,
  andon: status === "Downtime" ? "red" : "green",
  cycle_s: area === "molding" ? 72 : null,
  ideal_cycle_s: area === "molding" ? 68 : null,
  performance_pct: null,
  mix: area === "molding" ? { "FB-ARKA": 1 } : {},
  timer_min: null,
  shift: area === "molding" ? shift(86.7, 94.5) : shift(95, null),
  ...extra,
});

const SNAP = {
  session_id: "s",
  scenario: "baseline",
  sim_minute: 240,
  sim_ts: "2027-02-01T11:00+07:00",
  machines: [
    machine("IMM-04", "Injection molding 3,000 t #4", "molding", "Running"),
    machine("IMM-05", "Injection molding 3,000 t #5", "molding", "Downtime", { reason_code: "HEATER_BAND", timer_min: 12 }),
    machine("PNT-01", "Paint line #1", "painting", "Running"),
  ],
  events: [],
};
const WOS = { work_orders: [{ wo_id: "WO-0001", machine_id: "IMM-04", status: "In Production", children: [], output_qty: 900, qty: 1200, sku: "FB-ARKA", progress_pct: 75 }] };

beforeEach(() => {
  globalThis.fetch = vi.fn().mockImplementation(async (url: string) => {
    const body = url.includes("/api/twin/tick") ? SNAP : url.includes("/api/work-orders") ? WOS : { ai_enabled: false };
    return { ok: true, json: async () => body };
  });
});

it("shows one andon card per machine with its state, work order and shift OEE, filterable by area", async () => {
  render(
    <MemoryRouter>
      <AppProvider>
        <LiveBoardPage />
      </AppProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByText("Injection molding 3,000 t #4")).toBeInTheDocument();
  expect(screen.getByText("Downtime · heater band · 12 min")).toBeInTheDocument();
  expect(screen.getByText("Requires technician dispatch")).toBeInTheDocument();
  expect(await screen.findByText("WO-0001")).toBeInTheDocument();
  expect(screen.getAllByRole("img", { name: "OEE 86.7%" })).toHaveLength(2);
  expect(screen.getByRole("img", { name: "Avail. 95%" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Painting" }));
  expect(screen.queryByText("Injection molding 3,000 t #4")).not.toBeInTheDocument();
  expect(screen.getByText("Paint line #1")).toBeInTheDocument();
});
