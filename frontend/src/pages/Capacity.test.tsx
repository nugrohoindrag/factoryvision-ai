import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AppProvider } from "../lib/store";
import CapacityPage from "./Capacity";

const PAGE = {
  period_start: "2027-02-01",
  period_end: "2027-02-28",
  planning_utilization_pct: 80,
  working_days: 20,
  formulas: {},
  kpis: { demand: 1000, total: 800, planning: 640, buffer: 160, gap: 200, utilization_pct: 125, status: "Capacity Up Required" },
  rows: [
    {
      sku: "FB-ARKA",
      product_name: "Front ARKA",
      demand_qty: 1000,
      total: 800,
      planning: 640,
      buffer: 160,
      utilization_pct: 125,
      gap: 200,
      status: "Capacity Up Required",
      uncomputed: [],
      proposals: [{ action_id: "ACT-1", title: "Move FB-ARKA mold to IMM-05", status: "pending" }],
    },
  ],
};

function mockApi(settings: Record<string, unknown>) {
  const calls: { url: string; method: string; body?: unknown }[] = [];
  globalThis.fetch = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
    let body: unknown = {};
    if (url.includes("/api/mode")) body = { ai_enabled: false };
    else if (url.includes("/api/modules/planning/summary")) body = { current: { month: "2027-02-01" } };
    else if (url.startsWith("/api/capacity/settings") && method === "PUT") body = { ...settings, strict_process_sequence: true };
    else if (url.startsWith("/api/capacity/settings")) body = settings;
    else if (url.startsWith("/api/capacity/snapshots")) body = { snapshots: [] };
    else if (url.startsWith("/api/capacity?")) body = PAGE;
    return { ok: true, json: async () => body };
  });
  return calls;
}

const SETTINGS = { planning_utilization_pct: 80, default_planning_utilization_pct: 80, strict_process_sequence: false };

function renderPage() {
  render(
    <MemoryRouter initialEntries={["/capacity"]}>
      <AppProvider>
        <CapacityPage />
      </AppProvider>
    </MemoryRouter>,
  );
}

it("shows the pending proposal linked to a Capacity Up Required product", async () => {
  mockApi(SETTINGS);
  renderPage();
  expect(await screen.findByText("Proposals for the capacity gap")).toBeInTheDocument();
  expect(screen.getByText("Move FB-ARKA mold to IMM-05")).toBeInTheDocument();
  expect(screen.getByText("(pending)")).toBeInTheDocument();
});

it("turns strict process sequence on through the settings endpoint", async () => {
  const calls = mockApi(SETTINGS);
  renderPage();
  const box = await screen.findByRole("checkbox", { name: "Strict process sequence" });
  expect(box).not.toBeChecked();
  fireEvent.click(box);
  await waitFor(() => {
    const put = calls.find((c) => c.method === "PUT" && c.url === "/api/capacity/settings");
    expect(put?.body).toEqual({ strict_process_sequence: true });
  });
});
