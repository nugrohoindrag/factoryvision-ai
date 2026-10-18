import { expect, test } from "@playwright/test";

test("the shell renders without a backend", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Plant Briefing" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Modules" })).toBeVisible();
});
