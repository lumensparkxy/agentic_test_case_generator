import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { seedAuthenticatedSession } from "./support/auth.js";
import { installUseCaseReviewApi, useCaseProjectFixture, USE_CASE_PROJECT_ID } from "./support/use-case-review.js";
const issue = "TC-002 needs a clear expected result.";
const suiteIssue = "Add boundary coverage.";
const cases = [1, 2].map((n) => ({
	id: `TC-00${n}`,
	title: `Checkout ${n}`,
	tags: ["REQ-101"],
	steps: [{ step: 1, action: "Submit", expected: "Saved" }],
}));
async function open(page, options = {}) {
	const project = useCaseProjectFixture();
	project.current_snapshots.test_cases = {
		snapshot_id: "cases-v1",
		project_id: USE_CASE_PROJECT_ID,
		stage: "test_cases",
		version: 1,
		payload: {
			test_cases: cases,
			review: {
				approved: false,
				score: 65,
				threshold: 90,
				summary: "Improve expected results and coverage.",
				blocking_issues: [issue, suiteIssue],
				unmet_criteria: [issue],
			},
			...options.payload,
		},
	};
	project.stage_state.test_cases = { current_snapshot_id: "cases-v1", stale: Boolean(options.stale), approved: false, version: 1 };
	const api = await installUseCaseReviewApi(page, { initialProject: project });
	const requests = [];
	await page.route("**/testcases/refine", async (route) => {
		requests.push(route.request().postDataJSON());
		if (options.gate) await options.gate;
		const failure = options.failures?.shift();
		if (failure)
			return route.fulfill({
				status: failure,
				contentType: "application/json",
				body: JSON.stringify({ detail: "Could not apply fixes." }),
			});
		const payload = {
			...project.current_snapshots.test_cases.payload,
			review: options.resultReview || {
				approved: true,
				score: 95,
				threshold: 90,
				summary: "Quality targets met.",
				blocking_issues: [],
				unmet_criteria: [],
			},
			workflow_diagnostics: { status: "completed" },
		};
		project.current_revision += 1;
		project.current_snapshots.test_cases = { ...project.current_snapshots.test_cases, snapshot_id: "cases-v2", version: 2, payload };
		api.setProject(project);
		await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) });
	});
	await page.route("**/execution/preview", (route) =>
		route.fulfill({
			status: 200,
			contentType: "application/json",
			body: JSON.stringify({ executable: [], manual: [], unsupported: [], invalid: [], warnings: [], summary: {} }),
		})
	);
	await seedAuthenticatedSession(page);
	await page.goto(`/projects/${USE_CASE_PROJECT_ID}/test-cases`);
	await expect(page.getByRole("tab", { name: /^Improve tests/ })).toBeVisible();
	return { api, requests, project };
}
const panel = (page) => page.getByRole("region", { name: "Improve test quality" });
const apply = (page) => panel(page).getByRole("button", { name: "Apply selected fixes", exact: true });

test("failed review opens actionable findings; selection and instructions survive tab switches and navigate to the exact case", async ({
	page,
}) => {
	await open(page);
	await expect(page.getByRole("tab", { name: /^Improve tests/ })).toHaveAttribute("aria-selected", "true");
	await expect(panel(page).getByRole("checkbox")).toHaveCount(2);
	await expect(panel(page).getByRole("checkbox", { checked: true })).toHaveCount(0);
	await expect(apply(page)).toBeDisabled();
	await panel(page).getByRole("button", { name: "Select all", exact: true }).click();
	await expect(panel(page).getByRole("checkbox", { checked: true })).toHaveCount(2);
	await panel(page).getByRole("button", { name: "Clear selection", exact: true }).click();
	await panel(page).getByRole("checkbox", { name: issue, exact: true }).check();
	await panel(page).getByRole("textbox").fill("Use clear user-visible outcomes.");
	await panel(page).getByRole("button", { name: "View test case TC-002", exact: true }).click();
	await expect(page.getByRole("tabpanel", { name: "Generated Test Cases", exact: true })).toBeFocused();
	await expect(page.getByRole("region", { name: "Selected test case", exact: true })).toContainText("Checkout 2");
	await page.getByRole("tab", { name: /^Improve tests/ }).click();
	await expect(panel(page).getByRole("checkbox", { name: issue, exact: true })).toBeChecked();
	await expect(panel(page).getByRole("textbox")).toHaveValue("Use clear user-visible outcomes.");
	await expect(panel(page).getByRole("button", { name: /^View test case/ })).toHaveCount(1);
});

test("applies explicit selected feedback with project recommendations, refreshes quality and clears drafts", async ({ page }) => {
	const { requests } = await open(page);
	await panel(page).getByRole("checkbox", { name: issue, exact: true }).check();
	await panel(page).getByRole("textbox").fill("  Use visible outcomes.  ");
	await apply(page).click();
	await expect(panel(page).getByRole("heading", { name: "Machine quality check passed" })).toBeVisible();
	expect(requests).toHaveLength(1);
	expect(requests[0].feedback).toBe(
		`Address the requested fixes below. Preserve unrelated test cases and their IDs where possible.\n\nSelected findings:\n- ${issue}\n\nAdditional instructions:\nUse visible outcomes.`
	);
	expect(requests[0].base_project_revision).toBe(7);
	expect(requests[0].test_cases).toHaveLength(2);
	await expect(panel(page).getByRole("textbox")).toHaveValue("");
	await expect(panel(page)).toContainText("does not replace your review");
	await expect(page.getByRole("tab", { name: /^Improve tests/ })).toHaveAttribute("aria-selected", "true");
});

test("failed save preserves drafts and suite; retry succeeds with remaining findings", async ({ page }) => {
	const { requests } = await open(page, {
		failures: [503],
		resultReview: { approved: false, score: 80, threshold: 90, blocking_issues: [suiteIssue] },
	});
	await panel(page).getByRole("checkbox", { name: issue, exact: true }).check();
	await apply(page).click();
	await expect(panel(page).getByRole("alert")).toBeVisible();
	await expect(panel(page).getByRole("checkbox", { name: issue, exact: true })).toBeChecked();
	await apply(page).click();
	await expect(panel(page).getByRole("checkbox")).toHaveCount(1);
	await expect(panel(page).getByRole("checkbox")).not.toBeChecked();
	expect(requests[1].feedback).toBe(requests[0].feedback);
});

test("revision conflict requires reload and new selection", async ({ page }) => {
	await open(page, { failures: [409] });
	await panel(page).getByRole("checkbox", { name: issue, exact: true }).check();
	await apply(page).click();
	await expect(panel(page).getByRole("alert")).toContainText("project changed");
	await expect(apply(page)).toBeDisabled();
	await panel(page).getByRole("button", { name: "Reload current findings" }).click();
	await expect(panel(page).getByRole("checkbox", { name: issue, exact: true })).not.toBeChecked();
	await expect(apply(page)).toBeDisabled();
	await panel(page).getByRole("checkbox", { name: issue, exact: true }).check();
	await expect(apply(page)).toBeEnabled();
});

test("stale suite blocks refinement and exposes impact review", async ({ page }) => {
	const { requests } = await open(page, { stale: true });
	await expect(apply(page)).toBeDisabled();
	await expect(panel(page)).toContainText("Upstream inputs changed");
	await panel(page).getByRole("button", { name: "Review impact" }).click();
	await expect(page.locator(".generation-gate-card")).toContainText("impact analysis");
	expect(requests).toHaveLength(0);
});

test("instructions-only refinement works when findings are unavailable", async ({ page }) => {
	await open(page, { payload: { review: { approved: false, blocking_issues: [], unmet_criteria: [] } } });
	const button = panel(page).getByRole("button", { name: "Apply improvements" });
	await expect(button).toBeDisabled();
	await panel(page).getByRole("textbox").fill("Add edge cases.");
	await button.click();
	await expect(panel(page).getByRole("heading", { name: "Machine quality check passed" })).toBeVisible();
});

for (const width of [390, 1488])
	test(`Improve tests is accessible at ${width}px`, async ({ page }) => {
		await page.setViewportSize({ width, height: 900 });
		await open(page);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
		await expect(page.locator(".test-generation-panel")).toHaveCSS("opacity", "1");
		const result = await new AxeBuilder({ page }).include(".improve-tests").analyze();
		expect(result.violations).toEqual([]);
	});

test("busy refinement blocks duplicate requests and edits", async ({ page }) => {
	let release;
	const gate = new Promise((resolve) => {
		release = resolve;
	});
	const { requests } = await open(page, { gate });
	await panel(page).getByRole("checkbox", { name: issue, exact: true }).check();
	await apply(page).click();
	await expect(panel(page).getByRole("button", { name: "Applying fixes…" })).toBeDisabled();
	await expect(panel(page).getByRole("textbox")).toBeDisabled();
	await expect(panel(page).getByRole("checkbox", { name: issue, exact: true })).toBeDisabled();
	expect(requests).toHaveLength(1);
	release();
	await expect(panel(page).getByRole("heading", { name: "Machine quality check passed" })).toBeVisible();
});

test("a changed source snapshot resets selections and instructions", async ({ page }) => {
	const { api, project } = await open(page);
	await panel(page).getByRole("checkbox", { name: issue, exact: true }).check();
	await panel(page).getByRole("textbox").fill("Draft instructions");
	project.current_snapshots.test_cases.snapshot_id = "external-v2";
	project.current_revision += 1;
	api.setProject(project);
	await page
		.getByRole("navigation", { name: "Project navigation", exact: true })
		.getByRole("link", { name: /^Requirements,/ })
		.click();
	await page
		.getByRole("navigation", { name: "Project navigation", exact: true })
		.getByRole("link", { name: /^Test Cases,/ })
		.click();
	await expect(panel(page).getByRole("checkbox", { name: issue, exact: true })).not.toBeChecked();
	await expect(panel(page).getByRole("textbox")).toHaveValue("");
});
