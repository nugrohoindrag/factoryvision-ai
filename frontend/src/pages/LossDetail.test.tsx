import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AppProvider } from "../lib/store";
import LossDetailPage from "./LossDetail";

const LOSS = {
  id: "speed-IMM-04-FB-ARKA",
  kind: "speed",
  title: "Speed loss on IMM-04 (FB-ARKA)",
  loss_idr: 50517000,
  detail: "158 bumpers not molded; cycle 79.3 s vs ideal 68 s",
  day: "2027-01-30",
  rank: 1,
  share_of_top3_pct: 59.4,
  machine_id: "IMM-04",
  sku: "FB-ARKA",
  trace: {
    formula: "Loss = (actual machine time - ideal machine time) / ideal cycle x price x contribution margin",
    steps: [
      { label: "Time lost", expr: "75,776 - 65,008 s (3.0 h)", value: "10,768 s" },
      { label: "Loss", expr: "158.4 x Rp1,450,000 x 22%", value: "Rp50,517,000" },
    ],
    equation: [
      { label: "bumpers not molded", value: "158.4" },
      { label: "selling price", value: "Rp1,450,000" },
      { label: "contribution margin", value: "22%" },
    ],
    constants: [
      { name: "Selling price, FB-ARKA", value: "Rp1,450,000", source: "Product master · FB-ARKA", link: "/master/products" },
      { name: "Contribution margin", value: "22%", source: "Finance assumption, share of the selling price", link: null },
    ],
    scope: [
      { label: "Production day", value: "2027-01-30" },
      { label: "Machine", value: "IMM-04" },
      { label: "Product", value: "FB-ARKA" },
    ],
    dataset: "Production records per shift",
    rows: [{ production_date: "2027-01-30", shift_id: "A", machine_id: "IMM-04", sku: "FB-ARKA", total_quantity: 312, output_quantity: 300, scrap_quantity: 10, ideal_cycle_s: 68, actual_cycle_s: 79.18 }],
  },
};

beforeEach(() => {
  globalThis.fetch = vi.fn().mockImplementation(async (url: string) => {
    const body = url.includes("/api/briefing/losses/speed-IMM-04-FB-ARKA") ? LOSS : { ai_enabled: false };
    return { ok: true, json: async () => body };
  });
});

it("traces a briefing loss to its formula, constants and the fact rows it sums", async () => {
  render(
    <MemoryRouter initialEntries={["/losses/speed-IMM-04-FB-ARKA"]}>
      <AppProvider>
        <Routes>
          <Route path="/losses/:lossId" element={<LossDetailPage />} />
        </Routes>
      </AppProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByRole("heading", { name: "Speed loss on IMM-04 (FB-ARKA)" })).toBeInTheDocument();
  expect(screen.getByText("158.4 x Rp1,450,000 x 22%")).toBeInTheDocument();
  expect(screen.getByText("bumpers not molded")).toBeInTheDocument(); // the equation in the header
  expect(screen.getAllByText("Rp50,517,000")).toHaveLength(2); // last step and the equation's result
  expect(screen.getByRole("link", { name: "Product master · FB-ARKA" })).toHaveAttribute("href", "/master/products");
  expect(screen.getByText("Finance assumption, share of the selling price")).toBeInTheDocument();
  expect(screen.getByText("Production records per shift")).toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/fv\.|app\.plant|WHERE|_quantity|_cycle_s/); // no warehouse or code names reach the page
  expect(screen.getAllByText("3,488 s")).toHaveLength(2); // (79.18 - 68) x 312 s lost in shift A, and the total of one row
  expect(screen.getByRole("link", { name: "Plant Briefing" })).toHaveAttribute("href", "/");
});
