import { expect, test } from "@playwright/test";

test.skip(!process.env.FV_E2E_BASE_URL, "needs a running app: set FV_E2E_BASE_URL");

test("replay produces one plan, approving it updates the plant", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Plant Briefing" })).toBeVisible();
  await page.getByRole("button", { name: /Run Disruption Response/ }).click();
  await expect(page.getByRole("link", { name: /Review and approve/ })).toBeVisible({ timeout: 90_000 });
  await page.getByRole("link", { name: /Review and approve/ }).click();
  await page.getByRole("button", { name: /Approve plan/ }).click();
  await expect(page.getByText("approved").first()).toBeVisible();
});
