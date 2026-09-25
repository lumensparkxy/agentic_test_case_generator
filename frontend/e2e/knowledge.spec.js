import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { seedAuthenticatedSession } from "./support/auth.js";
import { installUseCaseReviewApi, USE_CASE_PROJECT_ID } from "./support/use-case-review.js";
const stages = ["requirements", "use_cases", "test_cases", "automation"];
async function openKnowledge(page, { failFirst = false, features = {}, initialEntries = [], validationError = null } = {}) {
	await installUseCaseReviewApi(page);
	await seedAuthenticatedSession(page);
	let entries = structuredClone(initialEntries);
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
		if (validationError) return route.fulfill({ status: 422, json: { detail: validationError } });
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

test("knowledge requires separate approval and edits preserve the active wording", { tag: [] }, async ({ page }) => {
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
				import_id: "guidance-preview",
				project_id: USE_CASE_PROJECT_ID,
				base_project_revision: 0,
				current_requirements: [],
				recovery_snapshot_ids: [],
				suggested_update_scope: [],
				warnings: [],
				candidates: [
					{
						candidate_id: "incoming-1",
						requirement: { id: "R1", text: "Remembered guidance was bypassed" },
						classification: "new",
						suggestions: [],
						reason: "New",
					},
				],
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
		skills_enabled: true,
		memory_enabled: true,
		omitted: [{ id: "stale-fact", revision: 3, reason: "needs_confirmation" }],
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
		route.fulfill({ json: { revision: "v2", entries: [], skills: [], features: { use_cases: { skills: false, memory: false } } } })
	);
	await page.route("**/knowledge/old/versions/1", (route) => route.fulfill({ json: { text: "Exact original wording" } }));
	await page.route("**/knowledge/deleted/versions/2", (route) => route.fulfill({ json: { unavailable: true } }));
	await page.goto(`/projects/${USE_CASE_PROJECT_ID}/use-cases`);
	await page.getByText("Guidance used · 1 skills · 2 knowledge entries", { exact: true }).click();
	await expect(page.getByText("Exact original wording", { exact: false })).toBeVisible();
	await expect(page.getByText("This saved guidance was deleted or is unavailable.", { exact: false })).toBeVisible();
	await expect(page.getByText(/Newer guidance available\. Existing artifact decisions/)).toBeVisible();
	await expect(page.getByLabel("Current human review status")).toHaveText("Human approved");
	await expect(page.getByText(/Recorded at run start — Skills: Enabled/)).toContainText("Approved context: Enabled");
	await expect(page.getByText(/Excluded: stale-fact/)).toContainText("needs confirmation");
	await expect(page.getByRole("region", { name: "Guidance for next generation" })).toContainText("Disabled by configuration");
	await page.setViewportSize({ width: 390, height: 844 });
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

const pilotFeatures = {
	requirements: { skills: true, memory: true },
	use_cases: { skills: true, memory: true },
	test_cases: { skills: false, memory: false },
	automation: { skills: false, memory: false },
};
function activeGuidance(overrides = {}) {
	return {
		id: "approved-fact",
		scope: "project",
		status: "active",
		revision: 1,
		active: {
			text: "Use Alice and Bob with Atlas and Birch",
			stages,
			revision: 1,
			kind: "convention",
			requirement_ids: [],
			source: "Manual guidance",
		},
		pending: null,
		selected_by_projects: [],
		...overrides,
	};
}
function availabilityCollection() {
	return {
		entries: [activeGuidance()],
		revision: "current",
		skills: stages.map((stage) => ({ id: `skill-${stage}`, stage, version: "1.0.0" })),
		features: structuredClone(pilotFeatures),
	};
}
async function installAvailability(page, collection = availabilityCollection()) {
	await installUseCaseReviewApi(page);
	await seedAuthenticatedSession(page);
	const state = { collection: structuredClone(collection), fail: false, requests: 0 };
	await page.route(`**/projects/${USE_CASE_PROJECT_ID}/knowledge`, (route) => {
		state.requests++;
		return state.fail
			? route.fulfill({ status: 503, json: { detail: "Knowledge storage unavailable" } })
			: route.fulfill({ json: state.collection });
	});
	return state;
}
const nextGuidance = (page) => page.getByRole("region", { name: "Guidance for next generation" });
const kindStatus = (page, index) => nextGuidance(page).locator(".guidance-availability-values > span").nth(index);

test("every generation workbench shows current guidance before actions, including held entry points", { tag: "@p1" }, async ({ page }) => {
	await installAvailability(page);
	for (const stage of stages) {
		await page.goto(`/projects/${USE_CASE_PROJECT_ID}/${stage.replaceAll("_", "-")}`);
		const group = nextGuidance(page);
		await expect(group).toBeVisible();
		if (["requirements", "use_cases"].includes(stage)) {
			await expect(kindStatus(page, 0)).toHaveText(/Skills:.*Enabled.*1 eligible/);
			await expect(kindStatus(page, 1)).toHaveText(/Approved context:.*Enabled.*1 eligible/);
		} else {
			await expect(kindStatus(page, 0)).toHaveText(/Skills:.*Disabled by configuration/);
			await expect(kindStatus(page, 1)).toHaveText(/Approved context:.*Disabled by configuration/);
			await expect(group).toContainText("Facts may still be carried through upstream artifacts");
			await expect(group).not.toContainText("0 eligible");
		}
	}
	await expect(nextGuidance(page)).toContainText("Execution preview is a deterministic check");
	await page.reload();
	await expect(kindStatus(page, 1)).toContainText("Disabled by configuration");
});

test("missing, independent and failed capability states never infer current flags or zero counts", { tag: "@p1" }, async ({ page }) => {
	const state = await installAvailability(page);
	await page.goto(`/projects/${USE_CASE_PROJECT_ID}/use-cases`);
	await expect(kindStatus(page, 1)).toContainText("Enabled");
	state.collection.features.use_cases = { skills: true, memory: false };
	await nextGuidance(page).getByRole("button").click();
	await expect(kindStatus(page, 0)).toContainText("Enabled");
	await expect(kindStatus(page, 1)).toContainText("Disabled by configuration");
	state.collection.features = {};
	await nextGuidance(page).getByRole("button").click();
	await expect(kindStatus(page, 0)).toHaveText(/Skills:.*Unavailable/);
	await expect(kindStatus(page, 1)).toHaveText(/Approved context:.*Unavailable/);
	await expect(nextGuidance(page)).not.toContainText("0 eligible");
	state.collection.features = structuredClone(pilotFeatures);
	await nextGuidance(page).getByRole("button").click();
	await expect(kindStatus(page, 1)).toContainText("Enabled");
	state.fail = true;
	await nextGuidance(page).getByRole("button").click();
	await expect(nextGuidance(page).getByText(/Guidance availability could not be checked/)).toBeVisible();
	await expect(kindStatus(page, 0)).toContainText("Unavailable");
	await expect(kindStatus(page, 1)).toContainText("Unavailable");
	await expect(nextGuidance(page)).not.toContainText("1 eligible");
	// A failed availability read is advisory; generation policy remains independent.
	await expect(page.getByRole("button", { name: "Regenerate Use Cases", exact: true })).toBeEnabled();
	state.fail = false;
	state.collection.entries = [];
	await nextGuidance(page).getByRole("button").click();
	await expect(kindStatus(page, 1)).toContainText("No eligible entries");
	await expect(kindStatus(page, 1)).toContainText("0 eligible");
	delete state.collection.entries;
	await nextGuidance(page).getByRole("button").click();
	await expect(kindStatus(page, 1)).toContainText("Unavailable");
	await expect(kindStatus(page, 1)).not.toContainText("0 eligible");
});

test(
	"eligibility excludes stale and proposed context while retaining the approved version of pending edits",
	{ tag: [] },
	async ({ page }) => {
		const approved = activeGuidance({
			active: { ...activeGuidance().active, stages: ["use_cases"] },
			pending: { ...activeGuidance().active, text: "Proposed future Automation convention", stages: ["automation"], revision: 2 },
		});
		const entries = [
			approved,
			activeGuidance({ id: "stale", status: "needs_confirmation" }),
			activeGuidance({ id: "suggested", status: "suggested", active: null, pending: activeGuidance().active }),
			activeGuidance({ id: "retired", status: "retired", active: null }),
			activeGuidance({ id: "other-stage", active: { ...activeGuidance().active, stages: ["requirements"] } }),
			activeGuidance({ id: "unselected", scope: "personal", selected: false }),
		];
		const state = await installAvailability(page, { ...availabilityCollection(), entries });
		await page.goto(`/projects/${USE_CASE_PROJECT_ID}/use-cases`);
		await expect(kindStatus(page, 1)).toHaveText(/Approved context:.*Enabled.*1 eligible/);
		state.collection.entries[0].status = "needs_confirmation";
		// Reload/focus refresh must not keep showing eligibility after a source change.
		await page.evaluate(() => window.dispatchEvent(new Event("focus")));
		await expect(kindStatus(page, 1)).toContainText("No eligible entries");
		await openKnowledge(page, { features: pilotFeatures, initialEntries: [approved] });
		const row = page.getByRole("row").filter({ hasText: "Proposed future Automation convention" });
		await expect(row).toContainText("Proposed: Automation");
		await expect(row).toContainText("Approved: Use Cases");
		await row.getByRole("button", { name: "Edit", exact: true }).click();
		const details = page.getByRole("group", { name: "Current effective eligibility", exact: true });
		await expect(details).toContainText("Use Cases: Eligible before run selection");
		await expect(details).toContainText("Test Cases: Disabled by configuration");
		await expect(details).toContainText("Automation: Not configured in the approved version");
		await expect(details).toContainText("Approved version 1: Use Alice and Bob with Atlas and Birch");
	}
);

test("enabled Test Cases counts shared planner guidance once and never leaks it into a held run", async ({ page }) => {
	const state = await installAvailability(page);
	state.collection.features.test_cases = { skills: true, memory: true };
	await page.goto(`/projects/${USE_CASE_PROJECT_ID}/test-cases`);
	await expect(kindStatus(page, 0)).toHaveText(/Skills:.*Enabled.*2 eligible/);
	await expect(kindStatus(page, 1)).toHaveText(/Approved context:.*Enabled.*1 eligible/);
	await expect(nextGuidance(page)).toContainText("may also use enabled Use Cases guidance");
	state.collection.features.use_cases = { skills: false, memory: false };
	await nextGuidance(page).getByRole("button").click();
	await expect(kindStatus(page, 0)).toHaveText(/Skills:.*Enabled.*1 eligible/);
	state.collection.features.test_cases = { skills: false, memory: false };
	state.collection.features.use_cases = { skills: true, memory: true };
	await nextGuidance(page).getByRole("button").click();
	await expect(kindStatus(page, 0)).toContainText("Disabled by configuration");
	await expect(kindStatus(page, 1)).toContainText("Disabled by configuration");
});

for (const width of [390, 1488])
	test(`visible stage guidance is accessible and fits at ${width}px`, async ({ page }) => {
		await page.setViewportSize({ width, height: 900 });
		await installAvailability(page);
		await page.goto(`/projects/${USE_CASE_PROJECT_ID}/test-cases`);
		await expect(kindStatus(page, 1)).toContainText("Disabled by configuration");
		await nextGuidance(page).screenshot({ path: test.info().outputPath(`stage-guidance-${width}.png`) });
		const results = await new AxeBuilder({ page }).include(".generation-guidance").analyze();
		expect(results.violations).toEqual([]);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
	});

for (const character of ["a", "🧪"]) {
	test(`guidance counts Unicode characters and preserves oversized ${character === "a" ? "ASCII" : "emoji"} input`, async ({ page }) => {
		const calls = await openKnowledge(page);
		await page.getByRole("button", { name: "Add guidance", exact: true }).click();
		const dialog = page.getByRole("dialog", { name: "Add guidance", exact: true });
		const input = dialog.getByLabel("Guidance", { exact: true });
		for (const count of [999, 1000, 1001]) {
			await input.fill(character.repeat(count));
			await expect(input).toHaveValue(character.repeat(count));
			await expect(dialog).toContainText(`Maximum 1000 characters · ${count} / 1000`);
			if (count > 1000) {
				await expect(input).toHaveAttribute("aria-invalid", "true");
				await expect(input).toHaveAccessibleDescription(/Maximum 1000 characters.*Guidance must contain no more than 1000 characters/s);
				await dialog.getByRole("button", { name: "Save proposal" }).click();
				await expect(input).toBeFocused();
				expect(calls).toHaveLength(0);
			} else {
				await expect(input).not.toHaveAttribute("aria-invalid", "true");
			}
		}
		await input.fill(character.repeat(1000));
		await expect(input).not.toHaveAttribute("aria-invalid", "true");
		await dialog.getByRole("button", { name: "Save proposal" }).click();
		await expect(dialog).toHaveCount(0);
		expect(calls).toHaveLength(1);
		expect(calls[0].payload.draft.text).toBe(character.repeat(1000));
	});
}

test("server text validation is associated with Guidance and does not discard input", { tag: [] }, async ({ page }) => {
	const calls = await openKnowledge(page, {
		validationError: [{ loc: ["body", "draft", "text"], type: "string_too_long", msg: "String should have at most 1000 characters" }],
	});
	await page.getByRole("button", { name: "Add guidance", exact: true }).click();
	const input = page.getByRole("dialog").getByLabel("Guidance", { exact: true });
	await input.fill("Guidance retained when a server rejects the field");
	await page.getByRole("dialog").getByRole("button", { name: "Save proposal" }).click();
	await expect(input).toHaveAttribute("aria-invalid", "true");
	await expect(input).toHaveAccessibleDescription(/Guidance must contain no more than 1000 characters/);
	await expect(input).toHaveValue("Guidance retained when a server rejects the field");
	expect(calls).toHaveLength(1);
	await input.fill("Revised guidance");
	await expect(input).not.toHaveAttribute("aria-invalid", "true");
});

for (const width of [390, 1488]) {
	test(`long guidance expands without squeezing status or losing dialog focus at ${width}px`, async ({ page }) => {
		await page.setViewportSize({ width, height: 900 });
		const entry = activeGuidance({ status: "needs_confirmation" });
		entry.active.text = "Use concrete outcomes for each behavior. ".repeat(22);
		await openKnowledge(page, { initialEntries: [entry], features: pilotFeatures });
		await page.getByLabel("Filter knowledge").selectOption("all");
		const region = page.getByRole("region", { name: "Knowledge entries", exact: true });
		await expect(region).toHaveAttribute("tabindex", "0");
		await expect(region.getByText(entry.active.text, { exact: true })).not.toBeVisible();
		const disclosure = region.getByText("Read full guidance", { exact: true });
		await disclosure.focus();
		await page.keyboard.press("Enter");
		await expect(region.getByText(entry.active.text, { exact: true })).toBeVisible();
		const statusCell = region.getByRole("cell", { name: "Needs confirmation", exact: true });
		expect(await statusCell.evaluate((node) => getComputedStyle(node).overflowWrap)).toBe("normal");
		expect((await statusCell.boundingBox()).width).toBeGreaterThan(130);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
		await page.getByRole("button", { name: "Add guidance", exact: true }).click();
		const dialog = page.getByRole("dialog", { name: "Add guidance", exact: true });
		const input = dialog.getByLabel("Guidance", { exact: true });
		await expect(input).toBeFocused();
		const dialogBox = await dialog.boundingBox();
		expect(dialogBox.x).toBeGreaterThanOrEqual(0);
		expect(dialogBox.x + dialogBox.width).toBeLessThanOrEqual(width);
		await input.fill("x".repeat(1001));
		await page.keyboard.press("Shift+Tab");
		await expect(dialog.getByRole("button", { name: "Save proposal" })).toBeFocused();
		await page.keyboard.press("Tab");
		await expect(input).toBeFocused();
		await expect(input).toHaveAccessibleDescription(/Maximum 1000 characters.*no more than 1000/s);
		const results = await new AxeBuilder({ page }).include(".knowledge-dialog").analyze();
		expect(results.violations).toEqual([]);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
		await page.keyboard.press("Escape");
		await expect(dialog).toHaveCount(0);
		await expect(page.getByRole("button", { name: "Add guidance", exact: true })).toBeFocused();
	});
}
