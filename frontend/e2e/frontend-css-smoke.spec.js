import { expect, test } from "@playwright/test";

import { expectNoDocumentOverflow } from "./support/layout";

const viewportCases = [
	{ label: "mobile", size: { width: 390, height: 844 } },
	{ label: "tablet", size: { width: 760, height: 900 } },
	{ label: "laptop", size: { width: 1280, height: 900 } },
	{ label: "desktop", size: { width: 1440, height: 900 } },
	{ label: "wide desktop", size: { width: 1920, height: 1080 } },
];

async function openWorkspaceControlsWhenCompact(page) {
	const toggle = page.getByRole("button", { name: /^Open workspace controls$/i });
	if (await toggle.isVisible().catch(() => false)) {
		await toggle.click();
		await expect(page.getByRole("button", { name: /^Close workspace controls$/i })).toBeVisible();
	}
}

test.describe("Frontend CSS smoke", () => {
	for (const { label, size } of viewportCases) {
		test(`renders auth and settings surfaces without overflow on ${label}`, async ({ page }) => {
			await page.setViewportSize(size);
			await page.goto("/");
			await openWorkspaceControlsWhenCompact(page);
			await expect(page.getByRole("button", { name: /^sign in$/i })).toBeVisible();
			await expectNoDocumentOverflow(page, `${label} auth`);

			await page.getByRole("button", { name: /settings/i }).click();
			await expect(page.getByRole("dialog", { name: /settings/i })).toBeVisible();
			await expectNoDocumentOverflow(page, `${label} workflow settings`);

			await page.getByRole("button", { name: /integrations/i }).click();
			await expect(page.getByRole("heading", { name: /integration connections/i })).toBeVisible();
			await expect(page.getByRole("heading", { name: /jira cloud/i })).toBeVisible();
			await expect(page.getByRole("heading", { name: /azure devops/i })).toBeVisible();
			await expectNoDocumentOverflow(page, `${label} integration settings`);
		});
	}
});
