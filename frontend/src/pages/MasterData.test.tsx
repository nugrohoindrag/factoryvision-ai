import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AppProvider } from "../lib/store";
import MasterDataPage from "./MasterData";

const col = (key: string, label: string, extra = {}) => ({ key, label, kind: "str", ref: null, required: false, derived: false, ...extra });

const VENDORS = {
  key: "vendors",
  label: "Vendors",
  section: "Partners",
  editable: true,
  pk: ["vendor_id"],
  columns: [col("vendor_id", "ID", { required: true }), col("vendor_name", "Name", { required: true }), col("lead_time_days", "Lead time (days)", { kind: "int" })],
  options: {},
  rows: [
    { _key: "VEN-ANDALAS", vendor_id: "VEN-ANDALAS", vendor_name: "PT Andalas Polimindo", lead_time_days: 7 },
    { _key: "VEN-NEW", vendor_id: "VEN-NEW", vendor_name: "PT Baru", lead_time_days: 3, _status: "created" },
  ],
  deleted: [{ _key: "VEN-OLD", vendor_id: "VEN-OLD", vendor_name: "PT Lama", lead_time_days: 9 }],
  source: "fv.dim_vendor",
};

const calls: { url: string; method: string; body?: unknown }[] = [];

function mockApi(over: (url: string, method: string) => unknown = () => undefined) {
  calls.length = 0;
  globalThis.fetch = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
    const o = over(url, method);
    if (o && typeof o === "object" && "ok" in (o as object)) return o;
    const body = o ?? (url.startsWith("/api/master-data/vendors") && method === "GET" ? VENDORS : url.startsWith("/api/mode") ? { ai_enabled: false } : { pending: 0, actions: [], _key: "VEN-X" });
    return { ok: true, json: async () => body };
  });
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppProvider>
        <Routes>
          <Route path="/master/:entity" element={<MasterDataPage />} />
        </Routes>
      </AppProvider>
    </MemoryRouter>,
  );
}

it("lists rows with their session status and the rows deleted in this session", async () => {
  mockApi();
  renderAt("/master/vendors");
  expect(await screen.findByText("Vendors (2)")).toBeInTheDocument();
  expect(screen.getByText("Created")).toBeInTheDocument();
  expect(screen.getByText("Deleted in this session (1)")).toBeInTheDocument();
  expect(screen.getByText("2 changed in this session")).toBeInTheDocument();
});

it("adds a row and shows every field error the server names", async () => {
  mockApi((url, method) =>
    method === "POST" && url === "/api/master-data/vendors"
      ? {
          ok: false,
          status: 400,
          statusText: "Bad Request",
          json: async () => ({ error: { code: "FV-MD-001", title: "Invalid master data", message: "1 field(s) need fixing", detail: { fields: [{ field: "vendor_name", message: "Name is required." }] } } }),
        }
      : undefined,
  );
  renderAt("/master/vendors");
  fireEvent.click(await screen.findByRole("button", { name: "+ Add" }));
  const dialog = screen.getByRole("dialog", { name: "Add to Vendors" });
  fireEvent.change(within(dialog).getByLabelText("ID"), { target: { value: "VEN-X" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Add" }));
  expect(await within(dialog).findByText("Name is required.")).toBeInTheDocument();
  expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ vendor_id: "VEN-X", vendor_name: "" });
});

it("edits a row with its key locked and sends it to the row's address", async () => {
  mockApi();
  renderAt("/master/vendors");
  fireEvent.click(await screen.findByRole("button", { name: "Edit VEN-ANDALAS" }));
  const dialog = screen.getByRole("dialog", { name: "Edit Vendors: VEN-ANDALAS" });
  expect(within(dialog).getByLabelText("ID")).toBeDisabled();
  fireEvent.change(within(dialog).getByLabelText("Lead time (days)"), { target: { value: "10" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
  expect(calls.find((c) => c.method === "PUT")).toMatchObject({ url: "/api/master-data/vendors/VEN-ANDALAS", body: { lead_time_days: "10" } });
});

it("deletes after a confirmation and restores a deleted row", async () => {
  mockApi();
  renderAt("/master/vendors");
  fireEvent.click(await screen.findByRole("button", { name: "Delete VEN-ANDALAS" }));
  fireEvent.click(within(screen.getByRole("dialog", { name: "Delete VEN-ANDALAS?" })).getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/master-data/vendors/VEN-ANDALAS")).toBe(true));
  fireEvent.click(await screen.findByRole("button", { name: "Restore" }));
  await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url === "/api/master-data/vendors/VEN-OLD/revert")).toBe(true));
});
