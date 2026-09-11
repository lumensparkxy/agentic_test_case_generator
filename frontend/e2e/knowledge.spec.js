import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { seedAuthenticatedSession } from "./support/auth.js";
import { installUseCaseReviewApi, USE_CASE_PROJECT_ID } from "./support/use-case-review.js";
const stages = ["requirements", "use_cases", "test_cases", "automation"];
async function openKnowledge(page, { failFirst = false, features = {} } = {}) {
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
					skills: [
						{ id: "scenario-coverage", version: "1.0.0", stage: "use_cases", description: "Source-grounded coverage" },
						{ id: "execution-ready-tests", version: "1.0.0", stage: "test_cases", description: "Concrete test steps" },
					],
					features,
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

test("skill catalog distinguishes enabled and held stages", async ({ page }) => {
	await openKnowledge(page, { features: { use_cases: { skills: true, memory: true }, test_cases: { skills: false, memory: false } } });
	const panel = page.getByRole("region", { name: "Project knowledge" });
	await panel.getByText("Skills used by this project", { exact: true }).click();
	const useCases = panel.locator("p").filter({ hasText: "scenario-coverage" });
	const testCases = panel.locator("p").filter({ hasText: "execution-ready-tests" });
	await expect(useCases).toContainText("Skills on");
	await expect(useCases).toContainText("Memory on");
	await expect(testCases).toContainText("Skills off");
	await expect(testCases).toContainText("Memory off");
});

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
		await page.screenshot({ path: test.info().outputPath(`knowledge-dialog-${width}.png`), fullPage: false });
		const results = await new AxeBuilder({ page }).include(".knowledge-dialog").analyze();
		expect(results.violations).toEqual([]);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
	});

test("generation retries the same request and bypasses memory only after an explicit choice", async ({ page }) => {
	const { sampleRequirementsFile } = await import("./support/auth.js");
	const { useCaseProjectFixture } = await import("./support/use-case-review.js");
	await installUseCaseReviewApi(page, {
		initialProject: useCaseProjectFixture({ snapshot: null, current_snapshots: {}, stage_state: {} }),
	});
	await seedAuthenticatedSession(page);
	const attempts = [];
	await page.route("**/requirements/parse", async (route) => {
		attempts.push(route.request().headers());
		if (!route.request().headers()["x-knowledge-bypass"])
			return route.fulfill({ status: 503, json: { detail: { code: "knowledge_unavailable", can_bypass: true } } });
		return route.fulfill({
			json: {
				source_name: "fixture",
				raw_text: "Source",
				requirements: [{ id: "R1", text: "Remembered guidance was bypassed", review_status: "Pending" }],
				guidance: { manifest_id: "bypassed", model: "fixture", memory_bypassed: true, memories: [], skills: [] },
			},
		});
	});
	await page.goto(`/projects/${USE_CASE_PROJECT_ID}/requirements`);
	await page.locator('input[type="file"]').setInputFiles(sampleRequirementsFile);
	await page.getByRole("button", { name: /^Parse Requirements$/i }).click();
	const dialog = page.getByRole("dialog", { name: "Remembered guidance could not be loaded" });
	await expect(dialog).toBeVisible();
	await expect(dialog.getByRole("button", { name: "Retry loading guidance" })).toBeFocused();
	await dialog.getByRole("button", { name: "Retry loading guidance" }).click();
	await expect.poll(() => attempts.length).toBe(2);
	await expect(dialog).toBeVisible();
	expect(attempts.every((a) => !a["x-knowledge-bypass"])).toBe(true);
	await dialog.getByRole("button", { name: "Run without remembered guidance" }).click();
	await expect.poll(() => attempts.length).toBe(3);
	expect(new Set(attempts.map((a) => a["x-request-id"])).size).toBe(1);
	expect(attempts[2]["x-knowledge-bypass"]).toBe("true");
	await expect(dialog).toHaveCount(0);
	await page.getByText("Guidance used · 0 skills · 0 knowledge entries", { exact: true }).click();
	await expect(page.getByText("Run without remembered guidance was explicitly selected.", { exact: false })).toBeVisible();
});

test("saved artifacts show historical wording, deleted entries, and newer guidance without changing review", async ({ page }) => {
	const { useCaseProjectFixture } = await import("./support/use-case-review.js");
	const project = useCaseProjectFixture({ reviewState: "approved" });
	project.current_snapshots.use_cases.metadata.guidance = {
		manifest_id: "recorded",
		model: "fixture-model",
		knowledge_revision: "v1",
		skills: [{ id: "scenario-coverage", version: "1.0.0", content_hash: "a".repeat(64) }],
		memories: [
			{ id: "old", revision: 1, source: "Review feedback" },
			{ id: "deleted", revision: 2, source: "Personal library" },
		],
	};
	await installUseCaseReviewApi(page, { initialProject: project });
	await seedAuthenticatedSession(page);
	await page.route(`**/projects/${USE_CASE_PROJECT_ID}/knowledge`, (route) =>
		route.fulfill({ json: { revision: "v2", entries: [], skills: [] } })
	);
	await page.route("**/knowledge/old/versions/1", (route) => route.fulfill({ json: { text: "Exact original wording" } }));
	await page.route("**/knowledge/deleted/versions/2", (route) => route.fulfill({ json: { unavailable: true } }));
	await page.goto(`/projects/${USE_CASE_PROJECT_ID}/use-cases`);
	await page.getByText("Guidance used · 1 skills · 2 knowledge entries", { exact: true }).click();
	await expect(page.getByText("Exact original wording", { exact: false })).toBeVisible();
	await expect(page.getByText("This saved guidance was deleted or is unavailable.", { exact: false })).toBeVisible();
	await expect(page.getByText(/Newer guidance available\. Existing artifact decisions/)).toBeVisible();
	await expect(page.getByLabel("Current human review status")).toHaveText("Human approved");
	await page.setViewportSize({ width: 390, height: 844 });
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
