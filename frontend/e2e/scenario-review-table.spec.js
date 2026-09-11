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
		const region = page.getByRole("region", { name: "Use cases for REQ-101", exact: true });
		await expect(region).toHaveAttribute("tabindex", "0");
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
		const results = await new AxeBuilder({ page }).include(".scenario-table-scroll").analyze();
		expect(results.violations).toEqual([]);
	});

test("column dividers support pointer and keyboard resizing without losing drafts", async ({ page }) => {
	await page.setViewportSize({ width: 1488, height: 900 });
	const api = await open(page);
	await status(page).selectOption("approved");
	const dividers = page.getByRole("separator", { name: "Resize Title / objective column", exact: true });
	const divider = dividers.first();
	const headers = page.getByRole("columnheader", { name: /^Title \/ objective/ });
	const header = headers.first();
	const table = page.getByRole("table", { name: "REQ-101", exact: true });
	const tableWidth = (await table.boundingBox()).width;
	await expect(table.getByRole("separator")).toHaveCount(3);
	await expect(page.getByRole("separator", { name: "Resize Quality flags column", exact: true })).toHaveCount(0);
	const before = (await header.boundingBox()).width;
	const neighborWidth = (await table.getByRole("columnheader", { name: /^Review status/ }).boundingBox()).width;
	const box = await divider.boundingBox();
	await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
	await page.mouse.down();
	await page.mouse.move(box.x + box.width / 2 + 100, box.y + box.height / 2, { steps: 5 });
	await page.mouse.up();
	expect((await header.boundingBox()).width).toBeCloseTo(before + Math.min(100, neighborWidth - 150), 0);
	expect((await table.boundingBox()).width).toBeCloseTo(tableWidth, 0);
	await divider.focus();
	await page.keyboard.press("Home");
	await expect(divider).toHaveAttribute("aria-valuenow", "200");
	await page.keyboard.press("ArrowRight");
	await expect(divider).toHaveAttribute("aria-valuenow", "210");
	await expect(dividers.nth(1)).toHaveAttribute("aria-valuenow", "210");
	expect((await headers.nth(1).boundingBox()).width).toBe((await header.boundingBox()).width);
	await expect(status(page)).toHaveValue("approved");
	expect((await table.boundingBox()).width).toBeCloseTo(tableWidth, 0);
	const search = page.getByRole("searchbox", { name: "Search use cases", exact: true });
	await search.fill("supervisor");
	await expect(dividers).toHaveCount(1);
	await divider.focus();
	await page.keyboard.press("ArrowRight");
	await search.fill("");
	await expect(dividers).toHaveCount(2);
	await expect(dividers.first()).toHaveAttribute("aria-valuenow", "220");
	await expect(dividers.nth(1)).toHaveAttribute("aria-valuenow", "220");
	await expect(status(page)).toHaveValue("approved");
	expect(api.requests.review).toHaveLength(0);
	await page.getByRole("button", { name: "Discard edits", exact: true }).click();
	await page.reload();
	await expect(divider).toHaveAttribute("aria-valuenow", "220");
	await expect(dividers.nth(1)).toHaveAttribute("aria-valuenow", "220");
	expect((await headers.nth(1).boundingBox()).width).toBe((await header.boundingBox()).width);
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

test("requirement headings replace repeated cells and filtering preserves cross-group drafts", async ({ page }) => {
	const api = await open(page);
	await expect(page.getByRole("table")).toHaveCount(2);
	await expect(page.getByRole("columnheader", { name: /^Source requirement/ })).toHaveCount(0);
	await expect(page.getByRole("heading", { name: "REQ-101", exact: true })).toHaveCount(1);
	await expect(page.getByText("Customers can complete express checkout with a saved payment method.", { exact: true })).toHaveCount(1);
	await expect(page.getByRole("table", { name: "REQ-101", exact: true }).getByRole("rowheader")).toHaveText([
		"REQ-101-SCN-01",
		"REQ-101-SCN-02",
	]);
	await status(page).selectOption("approved");
	const search = page.getByRole("searchbox", { name: "Search use cases", exact: true });
	await search.fill("supervisor");
	await expect(page.getByRole("table", { name: "REQ-101", exact: true })).toHaveCount(0);
	await page.getByRole("combobox", { name: "Review status for REQ-202-SCN-01", exact: true }).selectOption("request_changes");
	await search.fill("");
	await expect(status(page)).toHaveValue("approved");
	await page.getByRole("button", { name: "Save reviews", exact: true }).click();
	await expect(page.getByRole("status", { name: "Review outcome" })).toHaveText("Scenario reviews saved.");
	expect(api.requests.review[0].payload.scenario_reviews).toEqual([
		{ requirement_id: "REQ-101", scenario_id: "REQ-101-SCN-01", status: "approved", quality_flags: [] },
		{ requirement_id: "REQ-202", scenario_id: "REQ-202-SCN-01", status: "request_changes", quality_flags: [] },
	]);
	await page.getByRole("combobox", { name: "Filter by review status", exact: true }).selectOption("approved");
	await expect(page.getByRole("table")).toHaveCount(1);
	await expect(page.locator(".scenario-group-heading").first()).not.toContainText(/\d+ use cases?/);
	await search.fill("no matching use case");
	await expect(page.getByRole("table")).toHaveCount(0);
	await expect(page.getByText("No scenarios match these filters.")).toBeVisible();
});

test("long requirement headings and single-row groups wrap without page overflow", async ({ page }) => {
	const project = useCaseProjectFixture();
	const groups = project.current_snapshots.use_cases.payload.coverage_plan;
	groups[0].requirement_text = "Long requirement text ".repeat(30);
	groups[0].scenarios = groups[0].scenarios.slice(0, 1);
	await page.setViewportSize({ width: 390, height: 844 });
	await page.addInitScript(() => localStorage.setItem("tcg.columns.use-cases", JSON.stringify([220, 143, 352, 187, 198])));
	await open(page, { initialProject: project });
	await expect(page.getByText(groups[0].requirement_text.trim(), { exact: true })).toBeVisible();
	await expect(page.locator(".scenario-group-heading").first()).not.toContainText(/\d+ use cases?/);
	await expect(page.getByRole("table", { name: "REQ-101", exact: true }).getByRole("columnheader")).toHaveCount(4);
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("last internal divider changes Quality flags from the left without moving the outer edge", async ({ page }) => {
	await page.setViewportSize({ width: 1488, height: 900 });
	await open(page);
	const table = page.getByRole("table", { name: "REQ-101", exact: true });
	const divider = table.getByRole("separator", { name: "Resize Review status column", exact: true });
	const flags = table.getByRole("columnheader", { name: "Quality flags", exact: true });
	const original = await table.boundingBox();
	await divider.focus();
	await page.keyboard.press("End");
	expect((await flags.boundingBox()).width).toBeCloseTo(150, 0);
	const resized = await table.boundingBox();
	expect(resized.x + resized.width).toBeCloseTo(original.x + original.width, 0);
	await page.keyboard.press("ArrowRight");
	expect((await flags.boundingBox()).width).toBeCloseTo(150, 0);
	await page.keyboard.press("Home");
	await expect(divider).toHaveAttribute("aria-valuenow", "150");
	expect((await table.boundingBox()).width).toBeCloseTo(original.width, 0);
});

test("older narrow saved widths still fill the table container", async ({ page }) => {
	await page.setViewportSize({ width: 1488, height: 900 });
	await page.addInitScript(() => localStorage.setItem("tcg.columns.use-cases-grouped-v1", JSON.stringify([144, 336, 160, 160])));
	await open(page);
	const table = page.getByRole("table", { name: "REQ-101", exact: true });
	const region = page.getByRole("region", { name: "Use cases for REQ-101", exact: true });
	const outer = await region.boundingBox();
	const bounds = await table.boundingBox();
	expect(Math.abs(outer.x + outer.width - bounds.x - bounds.width)).toBeLessThanOrEqual(2);
});

test("tables fit browser resizing before and after persisted column edits", async ({ page }) => {
	await page.setViewportSize({ width: 1488, height: 900 });
	await open(page);
	const tables = page.getByRole("table");
	const region = page.getByRole("region", { name: "Use cases for REQ-101", exact: true });
	const checkFit = async () => {
		for (const table of await tables.all())
			await expect
				.poll(async () => {
					const expected = Math.max(800, await region.evaluate((node) => node.clientWidth));
					return Math.abs((await table.boundingBox()).width - expected);
				})
				.toBeLessThan(1);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
	};
	for (const width of [1150, 1488]) {
		await page.setViewportSize({ width, height: 900 });
		await checkFit();
	}
	await status(page).selectOption("approved");
	await tables.first().getByRole("separator", { name: "Resize Title / objective column", exact: true }).press("ArrowRight");
	const saved = await page.evaluate(() => localStorage.getItem("tcg.columns.use-cases-grouped-v1"));
	for (const width of [1150, 1800, 390, 1488]) {
		await page.setViewportSize({ width, height: 900 });
		await checkFit();
		await expect(status(page)).toHaveValue("approved");
		expect(await page.evaluate(() => localStorage.getItem("tcg.columns.use-cases-grouped-v1"))).toBe(saved);
		const first = await tables
			.first()
			.getByRole("columnheader")
			.evaluateAll((cells) => cells.map((cell) => cell.getBoundingClientRect().width));
		const second = await tables
			.nth(1)
			.getByRole("columnheader")
			.evaluateAll((cells) => cells.map((cell) => cell.getBoundingClientRect().width));
		expect(second).toEqual(first);
		for (const [i, minimum] of [140, 200, 150, 150].entries()) expect(first[i]).toBeGreaterThanOrEqual(minimum - 1);
	}
	await page.reload();
	await expect(status(page)).toBeVisible();
	await page.setViewportSize({ width: 1150, height: 900 });
	await checkFit();
});

test("requirement ID and text start inline and wrap within the group", async ({ page }) => {
	const project = useCaseProjectFixture();
	project.current_snapshots.use_cases.payload.coverage_plan[0].requirement_text =
		"The system shall allow a user to create a task with a title containing between 1 and 100 characters. ".repeat(4).trim();
	await open(page, { initialProject: project });
	const heading = page.locator(".scenario-group-heading").first();
	for (const width of [1488, 390]) {
		await page.setViewportSize({ width, height: 900 });
		await expect(heading).not.toContainText(/\d+ use cases?/);
		const layout = await heading.evaluate((node) => {
			const id = node.querySelector("h3").getBoundingClientRect();
			const range = document.createRange();
			range.selectNodeContents(node.querySelector("p"));
			const lines = [...range.getClientRects()];
			const bounds = node.getBoundingClientRect();
			return {
				sameLine: Math.abs(id.y - lines[0].y) < 3,
				inline: lines[0].x > id.right,
				wraps: lines.length > 1,
				contained: lines.every((line) => line.left >= bounds.left && line.right <= bounds.right),
			};
		});
		expect(layout).toEqual({ sameLine: true, inline: true, wraps: true, contained: true });
	}
});
