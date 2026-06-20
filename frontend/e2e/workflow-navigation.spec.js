import { expect, test } from "@playwright/test";

import { seedAuthenticatedSession } from "./support/auth.js";

function jsonResponse(route, payload, status = 200) {
	return route.fulfill({
		status,
		contentType: "application/json",
		body: JSON.stringify(payload),
	});
}

async function mockShell(page) {
	await page.route("**/auth/me", async (route) =>
		jsonResponse(route, {
			sub: "playwright-e2e-user",
			email: "playwright-e2e@example.com",
			name: "Playwright E2E",
			picture: null,
		})
	);
	await page.route("**/reports/usage/me", async (route) => jsonResponse(route, { groups: [] }));
	await page.route("**/entitlements/me", async (route) =>
		jsonResponse(route, {
			account: { plan_tier: "premium", support_contact_email: "hello@spica-digital.eu" },
			requirements: { remaining: 500, exhausted: false },
			test_cases: { remaining: 500, exhausted: false },
			wallet: { balance_units: 5000, balance_token_display: "5000" },
			shadow_mode: false,
		})
	);
	await page.route("**/integrations/**", async (route) => {
		const url = new URL(route.request().url());
		if (!url.pathname.startsWith("/integrations/")) {
			return route.fallback();
		}
		return jsonResponse(route, { connected: false, connection: null });
	});
	await page.route("**/projects", async (route) => jsonResponse(route, { projects: [] }));
}

test.describe("Workflow navigation", () => {
	test("left navigation switches workflow destinations without changing tab content behavior", async ({ page }) => {
		await mockShell(page);
		await seedAuthenticatedSession(page);
		await page.goto("/");

		const navigation = page.getByRole("navigation", { name: "Workflow navigation" });
		await expect(navigation).toBeVisible({ timeout: 30_000 });

		for (const label of ["Upload", "Context", "Template", "Generate", "Automation", "Export"]) {
			await expect(navigation.getByRole("button", { name: new RegExp(`^${label},`, "i") })).toBeVisible();
		}

		await expect(navigation.getByRole("button", { name: /^Upload, Active$/i })).toBeVisible();
		await expect(page.getByRole("heading", { name: /^Upload Requirements$/i })).toBeVisible();

		await navigation.getByRole("button", { name: /^Generate,/i }).click();
		await expect(navigation.getByRole("button", { name: /^Generate, Active$/i })).toBeVisible();
		await expect(page.getByRole("heading", { name: /^Generate Test Cases$/i })).toBeVisible();

		await navigation.getByRole("button", { name: /^Export,/i }).click();
		await expect(navigation.getByRole("button", { name: /^Export, Active$/i })).toBeVisible();
		await expect(page.getByRole("heading", { name: /^Export Test Cases$/i })).toBeVisible();
	});
});
