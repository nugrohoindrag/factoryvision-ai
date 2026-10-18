import { render, screen } from "@testing-library/react";
import App from "./App";

const BRIEFING = {
  as_of: "2027-02-01",
  yesterday: "2027-01-30",
  oee: { molding: { oee_pct: 83.8, availability_pct: 92.7, performance_pct: 92.3, quality_pct: 98 }, plant: { oee_pct: 86.3 }, worst_machine: "IMM-04", worst_machine_oee_pct: 77.2 },
  top_losses: [{ title: "Speed loss on IMM-04 (FB-ARKA)", loss_idr: 50517000, detail: "158 bumpers", kind: "speed" }],
  risks: [{ module: "mes", severity: "high", title: "IMM-04 breakdown risk in ~7 days", detail: "cycle rising" }],
};

function mockApi() {
  globalThis.fetch = vi.fn().mockImplementation(async (url: string) => {
    const body = url.includes("/api/briefing") ? BRIEFING : url.includes("/api/mode") ? { ai_enabled: false, replays: ["disruption_response"], scenarios: [], models: {}, caps: {} } : { pending: 0, actions: [] };
    return { ok: true, json: async () => body };
  });
}

it("shows the plant briefing with numbers from the API", async () => {
  mockApi();
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Plant Briefing" })).toBeInTheDocument();
  expect(await screen.findByText("83.8%")).toBeInTheDocument();
  expect(screen.getByText(/IMM-04 breakdown risk/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Run Disruption Response \(Replay\)/ })).toBeEnabled();
});

it("disables Live mode when the server has no AI", async () => {
  mockApi();
  render(<App />);
  expect(await screen.findByRole("button", { name: "Live with AI" })).toBeDisabled();
});

it("folds Master Data in the sidebar until it is opened", async () => {
  mockApi();
  render(<App />);
  const group = await screen.findByRole("button", { name: /Master Data/ });
  expect(group).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByRole("link", { name: "Bill of Materials" })).not.toBeInTheDocument();
  group.click();
  expect(await screen.findByRole("link", { name: "Bill of Materials" })).toHaveAttribute("href", "/master/bom");
});
