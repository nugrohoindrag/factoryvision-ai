import { defineConfig, devices } from "@playwright/test";

// Top of the test pyramid: browser flows against the running app.
// Set FV_E2E_BASE_URL to test a deployment instead of the local dev server.
const baseURL = process.env.FV_E2E_BASE_URL ?? "http://localhost:5173";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  use: { baseURL, trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "phone", use: { ...devices["Pixel 7"] } },
  ],
  webServer: process.env.FV_E2E_BASE_URL ? undefined : { command: "npm run dev", url: baseURL, reuseExistingServer: true },
});
