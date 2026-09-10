import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { seedAuthenticatedSession } from "./support/auth.js";
import { installUseCaseReviewApi, useCaseProjectFixture, USE_CASE_PROJECT_ID } from "./support/use-case-review.js";
const scenarioId = "REQ-101-SCN-01";
const status = (page) => page.getByRole("combobox", { name: `Review status for ${scenarioId}`, exact: true });
async function open(page, options = {}) {
	const api = await installUseCaseReviewApi(page, options);
	await seedAuthenticatedSession(page);
	await page.goto(`/projects/${USE_CASE_PROJECT_ID}/use-cases`);
	await expect(status(page)).toBeVisible();
	return api;
}

test("persists scenario status and multiple quality flags, filters rows and preserves full approval scope", async ({ page }) => {
	const api = await open(page);
	await status(page).selectOption("approved");
	const row = page.getByRole("row").filter({ has: status(page) });
	await row.getByRole("button", { name: `Quality flags for ${scenarioId}`, exact: true }).click();
	await row.getByRole("checkbox", { name: "Ambiguous", exact: true }).check();
	await row.getByRole("checkbox", { name: "Not testable", exact: true }).check();
	await expect(page.getByRole("button", { name: "Approve all", exact: true })).toBeDisabled();
	expect(api.requests.review).toHaveLength(0);
	await page.getByRole("button", { name: "Save reviews", exact: true }).click();
	await expect(page.getByRole("status", { name: "Review outcome" })).toHaveText("Scenario reviews saved.");
	expect(api.requests.review[0].payload.scenario_reviews).toEqual([
		{ requirement_id: "REQ-101", scenario_id: scenarioId, status: "approved", quality_flags: ["Ambiguous", "Not testable"] },
	]);
	await expect(page.getByText("1 of 4 approved", { exact: false })).toBeVisible();
	await page.reload();
	await expect(status(page)).toHaveValue("approved");
	await expect(row.getByRole("button", { name: `Quality flags for ${scenarioId}`, exact: true })).toContainText("(2)");
	await page.getByRole("combobox", { name: "Filter by review status", exact: true }).selectOption("approved");
	await expect(page.getByRole("table").getByRole("row")).toHaveCount(2);
	await page.getByRole("button", { name: "Quality flag filters", exact: true }).click();
	await page.getByRole("checkbox", { name: "Duplicate", exact: true }).check();
	await expect(page.getByText("No scenarios match these filters.")).toBeVisible();
	await page.getByRole("button", { name: "Approve all", exact: true }).click();
	const dialog = page.getByRole("dialog");
	await expect(dialog).toContainText("all 4 scenarios");
	await dialog.getByRole("button", { name: "Approve Use Cases", exact: true }).click();
	await expect(page.getByRole("status", { name: "Review outcome" })).toHaveText("Use Cases approved.");
	expect(
		Object.values(api.getProject().stage_state.use_cases.metadata.scenario_reviews.items).every((item) => item.status === "approved")
	).toBe(true);
});

test("retains drafts and retries the identical failed save", async ({ page }) => {
	const api = await open(page, { reviewScenarios: [{ status: 503 }, {}] });
	await status(page).selectOption("request_changes");
	await page.getByRole("button", { name: "Save reviews", exact: true }).click();
	await expect(page.getByRole("alert")).toContainText("unavailable");
	await expect(status(page)).toHaveValue("request_changes");
	await page.getByRole("button", { name: "Retry saving reviews", exact: true }).click();
	await expect(page.getByRole("status", { name: "Review outcome" })).toHaveText("Scenario reviews saved.");
	expect(api.requests.review[1].payload).toEqual(api.requests.review[0].payload);
	expect(api.requests.review[1].headers["x-request-id"]).toBe(api.requests.review[0].headers["x-request-id"]);
});

test("conflicts require reload and discard instead of silently rebasing draft decisions", async ({ page }) => {
	const api = await open(page, {
		reviewScenarios: [{ status: 409, payload: { detail: { message: "Another reviewer changed this version.", reload_required: true } } }],
	});
	await status(page).selectOption("approved");
	await page.getByRole("button", { name: "Save reviews", exact: true }).click();
	await expect(page.getByRole("alert")).toContainText("Another reviewer changed");
	await expect(page.getByRole("button", { name: "Save reviews", exact: true })).toBeDisabled();
	await page.getByRole("button", { name: "Reload latest and discard edits", exact: true }).click();
	await expect(status(page)).toHaveValue("needs_review");
	expect(api.requests.review).toHaveLength(1);
});

test("editing a previously approved version revokes its whole-version decision", async ({ page }) => {
	const api = await open(page, { initialProject: useCaseProjectFixture({ reviewState: "approved" }) });
	await expect(status(page)).toHaveValue("approved");
	await status(page).selectOption("needs_review");
	await page.getByRole("button", { name: "Save reviews", exact: true }).click();
	await expect(page.getByRole("status", { name: "Current human review status" })).toContainText("Awaiting human review");
	expect(api.getProject().stage_state.use_cases.approved).toBe(false);
});

for (const width of [390, 1488])
	test(`scenario controls are accessible and the table scrolls inside the page at ${width}px`, async ({ page }) => {
		await page.setViewportSize({ width, height: 900 });
		await open(page);
		const region = page.getByRole("region", { name: "Use case scenarios table", exact: true });
		await expect(region).toHaveAttribute("tabindex", "0");
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
		const results = await new AxeBuilder({ page }).include(".scenario-table-scroll").analyze();
		expect(results.violations).toEqual([]);
	});

test("column dividers support pointer and keyboard resizing without losing drafts", async ({ page }) => {
	await page.setViewportSize({ width: 1488, height: 900 });
	const api = await open(page);
	await status(page).selectOption("approved");
	const divider = page.getByRole("separator", { name: "Resize Title / objective column", exact: true });
	const header = page.getByRole("columnheader", { name: /^Title \/ objective/ });
	const before = (await header.boundingBox()).width;
	const box = await divider.boundingBox();
	await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
	await page.mouse.down();
	await page.mouse.move(box.x + box.width / 2 + 100, box.y + box.height / 2, { steps: 5 });
	await page.mouse.up();
	expect((await header.boundingBox()).width).toBeGreaterThan(before + 80);
	await divider.focus();
	await page.keyboard.press("Home");
	await expect(divider).toHaveAttribute("aria-valuenow", "200");
	await page.keyboard.press("ArrowRight");
	await expect(divider).toHaveAttribute("aria-valuenow", "210");
	await expect(status(page)).toHaveValue("approved");
	expect(api.requests.review).toHaveLength(0);
	await page.getByRole("button", { name: "Discard edits", exact: true }).click();
	await page.reload();
	await expect(divider).toHaveAttribute("aria-valuenow", "210");
	await page.setViewportSize({ width: 390, height: 844 });
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("quality dropdown does not expand rows and restores focus on Escape", async ({ page }) => {
	await open(page);
	const trigger = page.getByRole("button", { name: `Quality flags for ${scenarioId}`, exact: true });
	const row = page.getByRole("row").filter({ has: trigger });
	const height = (await row.boundingBox()).height;
	await trigger.click();
	const group = page.getByRole("group", { name: `Quality flags for ${scenarioId}`, exact: true });
	await expect(group).toBeVisible();
	await group.getByRole("checkbox", { name: "Duplicate", exact: true }).check();
	expect((await row.boundingBox()).height).toBe(height);
	await page.keyboard.press("Escape");
	await expect(group).not.toBeVisible();
	await expect(trigger).toBeFocused();
	await expect(trigger).toContainText("Quality flags (1)");
	await trigger.click();
	await page.setViewportSize({ width: 390, height: 844 });
	await expect(group).not.toBeVisible();
});
