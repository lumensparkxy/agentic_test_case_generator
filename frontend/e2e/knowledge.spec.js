import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { seedAuthenticatedSession } from "./support/auth.js";
import { installUseCaseReviewApi, USE_CASE_PROJECT_ID } from "./support/use-case-review.js";
const stages = ["requirements", "use_cases", "test_cases", "automation"];
async function openKnowledge(page, { failFirst = false } = {}) {
	await installUseCaseReviewApi(page);
	await seedAuthenticatedSession(page);
	let entries = [];
	const calls = [];
	let failed = false;
	const personal = {
		id: "personal-1",
		scope: "personal",
		project_id: null,
		revision: 2,
		status: "active",
		active: {
			text: "Use supervisor terminology",
			kind: "terminology",
			stages,
			requirement_ids: [],
			revision: 1,
			source: "Personal library",
		},
		pending: null,
		selected_by_projects: [],
	};
	await page.route("**/me/knowledge", (route) =>
		route.fulfill({ json: { entries: [personal], revision: "personal", skills: [], features: {} } })
	);
	await page.route(`**/projects/${USE_CASE_PROJECT_ID}/knowledge`, async (route) => {
		if (route.request().method() === "GET")
			return route.fulfill({
				json: {
					entries,
					revision: String(calls.length),
					skills: [{ id: "scenario-coverage", version: "1.0.0", stage: "use_cases", description: "Source-grounded coverage" }],
					features: {},
				},
			});
		const payload = route.request().postDataJSON();
		calls.push({ payload, key: route.request().headers()["x-request-id"] });
		if (failFirst && !failed) {
			failed = true;
			return route.fulfill({ status: 503, json: { detail: "Temporary knowledge failure" } });
		}
		let entry = entries.find((e) => e.id === payload.entry_id);
		if (payload.action === "propose") {
			entry = {
				id: "new-1",
				scope: "project",
				project_id: USE_CASE_PROJECT_ID,
				revision: 1,
				status: "suggested",
				active: null,
				pending: { ...payload.draft, source: "Manual guidance", revision: 1 },
				selected_by_projects: [],
			};
			entries.push(entry);
		}
		if (payload.action === "approve") {
			entry.active = entry.pending;
			entry.pending = null;
			entry.status = "active";
			entry.revision++;
		}
		if (payload.action === "revise") {
			entry.pending = { ...payload.draft, source: "Manual guidance", revision: entry.revision + 1 };
			entry.revision++;
		}
		if (payload.action === "select") {
			entry = { ...personal, selected: true };
			entries.push(entry);
		}
		if (payload.action === "unselect") {
			entries = entries.filter((e) => e.id !== payload.entry_id);
		}
		return route.fulfill({ json: entry });
	});
	await page.goto(`/projects/${USE_CASE_PROJECT_ID}/context`);
	await expect(page.getByRole("region", { name: "Project knowledge" })).toBeVisible();
	return calls;
}

test("knowledge requires separate approval and edits preserve the active wording", async ({ page }) => {
	const calls = await openKnowledge(page);
	await page.getByRole("button", { name: "Add guidance", exact: true }).click();
	const dialog = page.getByRole("dialog", { name: "Add guidance", exact: true });
	await dialog.getByLabel("Guidance", { exact: true }).fill("Include expired-token scenarios");
	await dialog.getByRole("button", { name: "Save proposal" }).click();
	await expect(page.getByRole("cell", { name: "Suggested", exact: true })).toBeVisible();
	await page.getByRole("button", { name: "Review", exact: true }).click();
	const approval = page.getByRole("dialog", { name: "Approve knowledge", exact: true });
	await expect(approval).toContainText("does not approve generated artifacts");
	await approval.getByRole("button", { name: "Approve knowledge", exact: true }).click();
	await page.getByLabel("Filter knowledge").selectOption("active");
	await expect(page.getByRole("cell", { name: "Active", exact: true })).toBeVisible();
	await page.getByRole("button", { name: "Edit", exact: true }).click();
	await page.getByRole("dialog").getByLabel("Guidance", { exact: true }).fill("Include expired and invalid-token scenarios");
	await page.getByRole("button", { name: "Save proposal" }).click();
	await expect(page.getByText("Active version: Include expired-token scenarios", { exact: true })).toBeVisible();
	expect(calls.map((c) => c.payload.action)).toEqual(["propose", "approve", "revise"]);
});

test("failed saves keep the dialog and reuse the same idempotency key", async ({ page }) => {
	const calls = await openKnowledge(page, { failFirst: true });
	await page.getByRole("button", { name: "Add guidance", exact: true }).click();
	await page.getByRole("dialog").getByLabel("Guidance", { exact: true }).fill("Include boundary coverage");
	await page.getByRole("button", { name: "Save proposal" }).click();
	await expect(page.getByRole("alert")).toContainText("Temporary knowledge failure");
	await page.getByRole("button", { name: "Save proposal" }).click();
	expect(calls[0]).toEqual(calls[1]);
});

test("personal guidance requires selection and keyboard dialogs restore focus", async ({ page }) => {
	const calls = await openKnowledge(page);
	const trigger = page.getByRole("button", { name: "Choose from personal library", exact: true });
	await trigger.click();
	const chooser = page.getByRole("dialog", { name: "Choose from personal library", exact: true });
	await chooser.getByRole("checkbox", { name: "Use supervisor terminology", exact: true }).click();
	await expect(chooser.getByRole("checkbox", { name: "Use supervisor terminology", exact: true })).toBeChecked();
	await page.keyboard.press("Escape");
	await expect(trigger).toBeFocused();
	await expect(page.getByRole("cell", { name: "Use supervisor terminology", exact: true })).toBeVisible();
	await page.getByRole("button", { name: "Remove from project", exact: true }).click();
	await expect(page.getByRole("cell", { name: "Use supervisor terminology", exact: true })).toHaveCount(0);
	expect(calls.map((c) => c.payload.action)).toEqual(["select", "unselect"]);
});

for (const width of [390, 1488])
	test(`knowledge dialogs and tables are accessible at ${width}px`, async ({ page }) => {
		await page.setViewportSize({ width, height: 900 });
		await openKnowledge(page);
		await page.getByRole("button", { name: "Add guidance", exact: true }).click();
		const results = await new AxeBuilder({ page }).include(".knowledge-dialog").analyze();
		expect(results.violations).toEqual([]);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
	});
