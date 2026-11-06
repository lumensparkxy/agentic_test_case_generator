import { expect, test } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { createDeferred } from "./support/workspace.js";
import { installUseCaseReviewApi, useCaseProjectFixture, USE_CASE_PROJECT_ID } from "./support/use-case-review.js";

const path = `/projects/${USE_CASE_PROJECT_ID}/use-cases`;
function staleProject() {
	const p = useCaseProjectFixture();
	p.stage_state.use_cases.stale = true;
	p.current_snapshots.requirements.payload.requirements = Array.from({ length: 10 }, (_, i) => ({
		id: `REQ-${i + 1}`,
		text: `Requirement ${i + 1}`,
		review_status: "Approved",
	}));
	p.current_snapshots.use_cases.payload.coverage_plan = p.current_snapshots.requirements.payload.requirements.slice(0, 8).map((r) => ({
		requirement_id: r.id,
		requirement_text: r.text,
		scenarios: [
			{
				id: `${r.id}-S1`,
				requirement_id: r.id,
				title: `Old ${r.id}`,
				objective: r.text,
				priority: "Medium",
				scenario_type: "Happy Path",
				must_have: true,
			},
		],
	}));
	return p;
}
function freshProject(old) {
	const p = structuredClone(old);
	p.current_revision++;
	const snapshot = structuredClone(p.current_snapshots.use_cases || useCaseProjectFixture().current_snapshots.use_cases);
	snapshot.snapshot_id = "generated-use-cases";
	snapshot.version = (p.current_snapshots.use_cases?.version || 0) + 1;
	snapshot.approved = false;
	snapshot.project_revision = p.current_revision;
	snapshot.payload.coverage_plan = p.current_snapshots.requirements.payload.requirements.map((r) => ({
		requirement_id: r.id,
		requirement_text: r.text,
		scenarios: [
			{
				id: `${r.id}-new`,
				requirement_id: r.id,
				title: `Fresh ${r.id}`,
				objective: r.text,
				priority: "Medium",
				scenario_type: "Happy Path",
				must_have: true,
			},
		],
	}));
	p.current_snapshots.use_cases = snapshot;
	p.stage_state.use_cases = {
		current_snapshot_id: snapshot.snapshot_id,
		version: snapshot.version,
		approved: false,
		stale: false,
		metadata: {},
	};
	return p;
}
async function setup(page, options = {}) {
	const project = options.project || staleProject();
	const api = await installUseCaseReviewApi(page, { initialProject: project });
	const requests = [];
	await page.route(`**${path}/generate`, async (route) => {
		requests.push(route.request().postDataJSON());
		if (options.gate) await options.gate;
		if (options.status)
			return route.fulfill({
				status: options.status,
				json: { detail: options.message || "Generation failed; previous snapshot preserved." },
			});
		const next = freshProject(project);
		if (options.guidance) next.current_snapshots.use_cases.metadata = { guidance: options.guidance };
		api.setProject(next);
		await route.fulfill({ json: next });
	});
	await seedAuthenticatedSession(page);
	await page.goto(path);
	await expect(page.getByRole("heading", { name: "Use Cases", exact: true })).toBeVisible();
	return { api, requests, project };
}

test("regenerates 8 groups into 10 using one revision-only request and requires review", { tag: "@p1" }, async ({ page }) => {
	const gate = createDeferred();
	const { requests, project } = await setup(page, { gate: gate.promise });
	await expect(page.getByText("Regeneration needed", { exact: true })).toBeVisible();
	await page.getByRole("button", { name: "Regenerate Use Cases", exact: true }).click();
	await expect(page.getByRole("button", { name: "Generating Use Cases…", exact: true })).toBeDisabled();
	await expect(page.getByText("Old REQ-1", { exact: true })).toBeVisible();
	await expect(page.getByRole("button", { name: "Request changes", exact: true })).toBeDisabled();
	expect(requests).toEqual([{ base_project_revision: project.current_revision }]);
	gate.resolve();
	await expect(page.getByText("Fresh REQ-10", { exact: true })).toBeVisible();
	await expect(page.getByText("Regeneration needed", { exact: true })).toHaveCount(0);
	await expect(page.getByLabel("Current human review status")).toContainText("Awaiting human review");
	await expect(page.getByRole("status").filter({ hasText: "New Use Cases are ready" })).toBeVisible();
	expect(requests).toHaveLength(1);
	await page.screenshot({ path: test.info().outputPath("regenerated-use-cases.png"), fullPage: true });
});

for (const status of [503, 409])
	test(`failed generation ${status} keeps the old snapshot and allows reload`, async ({ page }) => {
		const { requests } = await setup(page, { status });
		await page.getByRole("button", { name: "Regenerate Use Cases", exact: true }).click();
		await expect(page.getByRole("alert")).toContainText("previous snapshot preserved");
		await expect(page.getByText("Old REQ-1", { exact: true })).toBeVisible();
		await expect(page.getByText("Regeneration needed", { exact: true })).toBeVisible();
		await expect(page.getByRole("button", { name: "Reload latest", exact: true })).toBeEnabled();
		await expect(page.getByRole("button", { name: "Regenerate Use Cases", exact: true })).toBeEnabled();
		expect(requests).toHaveLength(1);
	});

test("offers generation directly when no snapshot exists", async ({ page }) => {
	const project = staleProject();
	delete project.current_snapshots.use_cases;
	delete project.stage_state.use_cases;
	await setup(page, { project });
	await page.getByRole("button", { name: "Generate Use Cases", exact: true }).click();
	await expect(page.getByText("Fresh REQ-10", { exact: true })).toBeVisible();
	await expect(page.getByRole("heading", { name: "No Use Cases snapshot" })).toHaveCount(0);
});

test("guidance stays singular across first generation, review and reload without React key errors", { tag: "@p1" }, async ({ page }) => {
	const errors = [];
	page.on("console", (message) => {
		if (message.type() === "error" && /same key|unique.*key|children.*key/i.test(message.text())) errors.push(message.text());
	});
	page.on("pageerror", (error) => errors.push(error.message));
	const project = staleProject();
	delete project.current_snapshots.use_cases;
	delete project.stage_state.use_cases;
	const gate = createDeferred();
	await page.route(`**/projects/${USE_CASE_PROJECT_ID}/knowledge`, (route) =>
		route.fulfill({ json: { entries: [], skills: [], features: {}, revision: "0" } })
	);
	await setup(page, { project, gate: gate.promise, guidance: { model: "fixture-model", skills: [], memories: [] } });
	await expect(page.getByText("Guidance not recorded", { exact: true })).toHaveCount(0);
	await page.getByRole("button", { name: "Generate Use Cases", exact: true }).click();
	await expect(page.getByRole("button", { name: "Generating Use Cases…", exact: true })).toBeDisabled();
	await expect(page.getByText("Guidance not recorded", { exact: true })).toHaveCount(0);
	gate.resolve();
	const guidance = page.getByText("Guidance used · 0 skills · 0 knowledge entries", { exact: true });
	await expect(guidance).toHaveCount(1);
	await expect(page.getByText("Guidance not recorded", { exact: true })).toHaveCount(0);
	await page
		.getByRole("region", { name: "Use Cases review actions" })
		.getByRole("button", { name: "Request changes", exact: true })
		.click();
	const form = page.getByRole("form", { name: "Human review decision" });
	await form.getByRole("textbox", { name: /Review comment/ }).fill("Synthetic review: correct the duplicate scenario mapping.");
	await form.getByRole("button", { name: "Request changes", exact: true }).click();
	await expect(page.getByLabel("Current human review status")).toHaveText("Changes requested");
	await expect(guidance).toHaveCount(1);
	await expect(page.getByText("Guidance not recorded", { exact: true })).toHaveCount(0);
	await page.reload();
	await expect(guidance).toHaveCount(1);
	await expect(page.getByLabel("Current human review status")).toHaveText("Changes requested");
	expect(errors).toEqual([]);
});

test("an existing artifact without a guidance manifest reports that absence once", async ({ page }) => {
	await setup(page);
	await expect(page.getByText("Guidance not recorded", { exact: true })).toHaveCount(1);
});

test("unapproved requirements disable regeneration with a recovery destination", { tag: "@p1" }, async ({ page }) => {
	const project = staleProject();
	project.stage_state.requirements.approved = false;
	const { requests } = await setup(page, { project });
	await expect(page.getByRole("button", { name: "Regenerate Use Cases", exact: true })).toBeDisabled();
	await expect(page.getByRole("link", { name: "Open Requirements", exact: true })).toHaveAttribute(
		"href",
		`/projects/${USE_CASE_PROJECT_ID}/requirements`
	);
	expect(requests).toHaveLength(0);
});

test("late success after route navigation does not replace the active screen", async ({ page }) => {
	const gate = createDeferred();
	const { requests } = await setup(page, { gate: gate.promise });
	await page.getByRole("button", { name: "Regenerate Use Cases", exact: true }).click();
	await expect.poll(() => requests.length).toBe(1);
	await page.getByRole("navigation", { name: "Global navigation" }).getByRole("link", { name: "Home", exact: true }).click();
	await expect(page.getByRole("heading", { name: "Home", exact: true })).toBeVisible();
	const response = page.waitForResponse((r) => r.url().endsWith(`${path}/generate`));
	gate.resolve();
	await response;
	await expect(page.getByRole("heading", { name: "Home", exact: true })).toBeVisible();
	await expect(page.getByText(/New Use Cases are ready/)).toHaveCount(0);
});
