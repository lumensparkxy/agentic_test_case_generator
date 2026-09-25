import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { seedAuthenticatedSession } from "./support/auth.js";
import { installUseCaseReviewApi, useCaseProjectFixture, USE_CASE_PROJECT_ID } from "./support/use-case-review.js";
import { createDeferred } from "./support/workspace.js";

const projectPath = `/projects/${USE_CASE_PROJECT_ID}`;
const result = {
	id: "TC-CHANGED",
	title: "Changed checkout coverage",
	steps: [{ step: 1, action: "Submit checkout", expected: "Order is confirmed" }],
	linked_requirement_ids: ["REQ-101"],
	status: "In Review",
};
function fixture() {
	const project = useCaseProjectFixture();
	project.current_snapshots.test_cases = {
		snapshot_id: "tests-before",
		stage: "test_cases",
		payload: { test_cases: [{ ...result, id: "TC-KEPT", title: "Unchanged checkout coverage" }], approved: false },
	};
	project.stage_state.test_cases = { current_snapshot_id: "tests-before", approved: false, stale: false };
	project.current_snapshots.impact_analysis = {
		snapshot_id: "analysis-one",
		source_snapshot_id: "tests-before",
		payload: {
			changed_items: [],
			recommendations: Array.from({ length: 27 }, (_, index) => ({
				recommendation_id: `rec-${index}`,
				action: index < 25 ? "keep" : "add",
				title: index < 25 ? `Keep regression ${index + 1}` : `Add checkout coverage ${index - 24}`,
				reason: index < 25 ? "No impact detected." : "Cover changed checkout behavior.",
				accepted: true,
				confidence: 0.91,
			})),
			summary: { recommendation_counts: { keep: 25, add: 2 } },
		},
	};
	project.stage_state.impact_analysis = { current_snapshot_id: "analysis-one", stale: false };
	return project;
}
async function setup(page, { mode = "success", initialStatus, defaultAccepted = true } = {}) {
	let project = fixture();
	if (!defaultAccepted)
		project.current_snapshots.impact_analysis.payload.recommendations.forEach((item) => {
			item.accepted = false;
		});
	let requests = 0;
	let failRead = false;
	const gate = createDeferred();
	const apis = [];
	const publish = () => apis.forEach((api) => api.setProject(project));
	function receipt(status) {
		project.impact_application = {
			analysis_snapshot_id: "analysis-one",
			status,
			started_at: new Date().toISOString(),
			accepted_recommendation_ids: project.current_snapshots.impact_analysis.payload.recommendations.map((r) => r.recommendation_id),
			error: status === "failed" ? "Application failed before saving changes. The existing suite was preserved." : null,
		};
	}
	function complete() {
		receipt("applied");
		Object.assign(project.impact_application, {
			completed_at: "2026-09-22T10:15:00Z",
			result_snapshot_id: "tests-after",
			changed_test_case_ids: ["TC-CHANGED"],
			preserved_count: 1,
			updated_count: 0,
			added_count: 1,
			deprecated_count: 0,
		});
		project.current_revision++;
		project.current_snapshots.test_cases = {
			snapshot_id: "tests-after",
			stage: "test_cases",
			payload: { test_cases: [...project.current_snapshots.test_cases.payload.test_cases, result], approved: false },
		};
		publish();
	}
	if (initialStatus === "applied") complete();
	else if (initialStatus) receipt(initialStatus);
	async function install(target) {
		apis.push(await installUseCaseReviewApi(target, { initialProject: project }));
		await target.route(`**${projectPath}`, async (route) =>
			failRead ? route.fulfill({ status: 503, json: { detail: "offline" } }) : route.fallback()
		);
		await target.route(`**${projectPath}/impact-update/apply`, async (route) => {
			requests++;
			expect(route.request().postDataJSON().analysis_snapshot_id).toBe("analysis-one");
			receipt("applying");
			publish();
			await gate.promise;
			if (mode === "failure" && requests === 1) {
				receipt("failed");
				publish();
				return route.fulfill({ status: 500, json: { detail: "Generation failed" } });
			}
			complete();
			if (mode === "lost") return route.abort("connectionreset");
			return route.fulfill({ json: project });
		});
		await seedAuthenticatedSession(target);
		await target.goto(`${projectPath}/test-cases`);
		await expect(target.getByRole("heading", { name: "Impact Analysis", exact: true })).toBeVisible();
	}
	await install(page);
	return {
		install,
		complete,
		gate,
		requests: () => requests,
		newAnalysis: () => {
			project.current_revision++;
			project.impact_application = null;
			project.current_snapshots.impact_analysis.snapshot_id = "analysis-two";
			publish();
		},
		failReads: (value) => {
			failRead = value;
		},
	};
}
const bar = (page) => page.getByRole("region", { name: "Recommendation application" });

test("keyboard and rapid activation submit once, show persistent receipt, and filter changed tests", { tag: "@p1" }, async ({ page }) => {
	const errors = [];
	page.on("pageerror", (error) => errors.push(error.message));
	await page.setViewportSize({ width: 1440, height: 1000 });
	const flow = await setup(page);
	await expect(bar(page)).toContainText("25 keep unchanged · 2 add coverage");
	await page.getByRole("checkbox", { name: "Accept Add checkout coverage 2", exact: true }).scrollIntoViewIfNeeded();
	const sticky = await bar(page).boundingBox();
	expect(sticky.y).toBeGreaterThanOrEqual(0);
	expect(sticky.y + sticky.height).toBeLessThan(1000);
	await page.evaluate(() => window.scrollTo(0, 0));
	const apply = bar(page).getByRole("button", { name: "Apply 27 recommendations", exact: true });
	await apply.focus();
	await page.keyboard.press("Enter");
	await page.keyboard.press("Enter");
	await expect(bar(page).getByRole("button", { name: "Applying…", exact: true })).toBeDisabled();
	await expect(page.getByRole("checkbox", { name: "Accept Keep regression 1", exact: true })).toBeDisabled();
	await expect(bar(page)).toContainText("Your request was received.");
	expect(flow.requests()).toBe(1);
	await page.screenshot({ animations: "disabled", path: test.info().outputPath("impact-applying-desktop.png") });
	flow.gate.resolve();
	await expect(bar(page)).toContainText("Recommendations applied");
	await expect(bar(page)).toContainText("1 preserved · 0 updated · 1 added · 0 deprecated");
	await expect(page.locator(".impact-recommendation-meta").first()).toContainText("Applied");
	await page.reload();
	await expect(bar(page).getByRole("button", { name: "Review changed tests" })).toBeVisible();
	await page.screenshot({ animations: "disabled", path: test.info().outputPath("impact-applied-desktop.png") });
	await bar(page).getByRole("button", { name: "Review changed tests" }).click();
	await expect(page.locator("#generate-result-panel")).toBeFocused();
	await expect(page.getByRole("button", { name: /TC-CHANGED/ })).toBeVisible();
	await expect(page.getByRole("button", { name: /TC-KEPT/ })).toHaveCount(0);
	await page.getByRole("button", { name: "Show all tests" }).click();
	await expect(page.getByRole("button", { name: /TC-KEPT/ })).toBeVisible();
	expect(flow.requests()).toBe(1);
	expect(errors).toEqual([]);
});

test("refresh and a second tab recover the running operation without submitting again", { tag: "@p1" }, async ({ page, context }) => {
	const flow = await setup(page);
	await bar(page).getByRole("button", { name: "Apply 27 recommendations" }).click();
	await expect.poll(flow.requests).toBe(1);
	const second = await context.newPage();
	await flow.install(second);
	await expect(bar(second)).toContainText("Your request was received.");
	await page.reload();
	await expect(bar(page).getByRole("button", { name: "Applying…", exact: true })).toBeDisabled();
	flow.gate.resolve();
	await expect(bar(second)).toContainText("Recommendations applied");
	await expect(bar(page)).toContainText("Recommendations applied");
	expect(flow.requests()).toBe(1);
});

test("lost response reconciles saved success", { tag: "@p1" }, async ({ page }) => {
	const flow = await setup(page, { mode: "lost" });
	await bar(page).getByRole("button", { name: "Apply 27 recommendations" }).click();
	flow.gate.resolve();
	await expect(bar(page)).toContainText("Recommendations applied");
	await expect(bar(page).getByRole("button", { name: "Retry" })).toHaveCount(0);
	expect(flow.requests()).toBe(1);
});

test("confirmed failure permits one guarded retry", { tag: [] }, async ({ page }) => {
	const flow = await setup(page, { mode: "failure" });
	await bar(page).getByRole("button", { name: "Apply 27 recommendations" }).click();
	flow.gate.resolve();
	await expect(bar(page)).toContainText("Recommendations were not applied");
	await page.reload();
	await bar(page).getByRole("button", { name: "Retry", exact: true }).click();
	await expect(bar(page)).toContainText("Recommendations applied");
	expect(flow.requests()).toBe(2);
});

test("status outage keeps apply locked and check status recovers; mobile and accessibility", async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.emulateMedia({ reducedMotion: "reduce" });
	const flow = await setup(page, { initialStatus: "applying" });
	flow.failReads(true);
	await expect(bar(page)).toContainText("Application status unavailable");
	await expect(bar(page).getByRole("button", { name: /Apply / })).toHaveCount(0);
	await page.screenshot({ animations: "disabled", path: test.info().outputPath("impact-uncertain-mobile.png") });
	flow.complete();
	flow.failReads(false);
	await bar(page).getByRole("button", { name: "Check status" }).click();
	await expect(bar(page)).toContainText("Recommendations applied");
	await page.screenshot({ animations: "disabled", path: test.info().outputPath("impact-applied-mobile.png") });
	const bounds = await bar(page).boundingBox();
	expect(bounds.x).toBeGreaterThanOrEqual(0);
	expect(bounds.x + bounds.width).toBeLessThanOrEqual(390);
	const a11y = await new AxeBuilder({ page }).include(".impact-analysis-panel").analyze();
	expect(a11y.violations).toEqual([]);
	expect(flow.requests()).toBe(0);
});

test("a new analysis has a fresh action and unverifiable history never claims success", async ({ page }) => {
	const flow = await setup(page, { initialStatus: "applied" });
	await expect(bar(page)).toContainText("Recommendations applied");
	flow.newAnalysis();
	await page.reload();
	await expect(bar(page).getByRole("button", { name: "Apply 27 recommendations" })).toBeEnabled();
	expect(flow.requests()).toBe(0);
});

test("legacy evidence without proof requires analysis, not retry", async ({ page }) => {
	await setup(page, { initialStatus: "verification_required" });
	await expect(bar(page)).toContainText("Application status needs verification");
	await expect(bar(page).getByRole("button", { name: "Analyze impact again" })).toBeEnabled();
	await expect(bar(page).getByRole("button", { name: "Retry" })).toHaveCount(0);
});

test("late application response cannot overwrite a different project", { tag: "@p1" }, async ({ page }) => {
	const flow = await setup(page);
	await bar(page).getByRole("button", { name: "Apply 27 recommendations" }).click();
	await expect.poll(flow.requests).toBe(1);
	const other = useCaseProjectFixture({ projectId: "other-project" });
	other.project_id = "other-project";
	other.name = "Other project";
	await installUseCaseReviewApi(page, { initialProject: other });
	await page.evaluate(() => {
		window.history.pushState({}, "", "/projects/other-project/test-cases");
		window.dispatchEvent(new PopStateEvent("popstate"));
	});
	await expect(page.getByRole("button", { name: "Open QA project menu" })).toContainText("Other project");
	const reply = page.waitForResponse((response) => response.url().endsWith("/impact-update/apply"));
	flow.gate.resolve();
	await reply;
	await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
	await expect(bar(page)).toHaveCount(0);
	await expect(page.getByRole("button", { name: "Open QA project menu" })).toContainText("Other project");
});

test("failed repair restores the saved selection even when analysis defaults were unselected", async ({ page }) => {
	const flow = await setup(page, { initialStatus: "failed", defaultAccepted: false });
	await page.reload();
	await expect(page.getByRole("checkbox", { name: "Accept Keep regression 1", exact: true })).toBeChecked();
	await expect(bar(page).getByRole("button", { name: "Retry", exact: true })).toBeEnabled();
	await bar(page).getByRole("button", { name: "Retry", exact: true }).click();
	flow.gate.resolve();
	await expect(bar(page)).toContainText("Recommendations applied");
	expect(flow.requests()).toBe(1);
});

test("an already-open tab reconciles another tab's operation when focused", async ({ page, context }) => {
	const flow = await setup(page);
	const second = await context.newPage();
	await flow.install(second);
	await expect(bar(second).getByRole("button", { name: "Apply 27 recommendations" })).toBeEnabled();
	await bar(page).getByRole("button", { name: "Apply 27 recommendations" }).click();
	await expect.poll(flow.requests).toBe(1);
	await second.evaluate(() => window.dispatchEvent(new Event("focus")));
	await expect(bar(second)).toContainText("Your request was received.");
	flow.gate.resolve();
	await expect(bar(second)).toContainText("Recommendations applied");
	expect(flow.requests()).toBe(1);
});
