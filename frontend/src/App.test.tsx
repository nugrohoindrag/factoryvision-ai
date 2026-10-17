import { render, screen } from "@testing-library/react";
import App from "./App";

it("renders the product name and API status", async () => {
  globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: "ok", warehouse: "local" }) });
  render(<App />);
  expect(screen.getByRole("heading", { name: "Factory Vision AI" })).toBeInTheDocument();
  expect(await screen.findByText("API: ok (local)")).toBeInTheDocument();
});
