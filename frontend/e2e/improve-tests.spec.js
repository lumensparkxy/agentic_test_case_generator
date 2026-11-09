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
	const project = useCaseProjectFixture({ reviewState: options.useCaseReviewState });
	project.current_snapshots.test_cases = {
		snapshot_id: "cases-v1",
		project_id: USE_CASE_PROJECT_ID,
		stage: "test_cases",
		operation: options.operation,
		metadata: options.metadata,
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

test("recovers a lost suite-wide improvement response with the same request ID", { tag: "@p1" }, async ({ page }) => {
	const { api, project } = await open(page);
	const attempts = [];
	const changedCase = { ...cases[1], title: "Check the saved confirmation" };
	const payload = { ...project.current_snapshots.test_cases.payload, test_cases: [cases[0], changedCase] };
	await page.route("**/testcases/refine", async (route) => {
		const request = route.request();
		attempts.push({ id: request.headers()["x-request-id"], body: request.postDataJSON() });
		if (attempts.length === 1) {
			const saved = structuredClone(project);
			saved.current_revision++;
			saved.current_snapshots.test_cases = { ...saved.current_snapshots.test_cases, snapshot_id: "cases-v2", payload };
			saved.stage_state.test_cases.current_snapshot_id = "cases-v2";
			api.setProject(saved);
			return route.abort("failed");
		}
		await route.fulfill({ json: payload });
	});
	await panel(page).getByRole("checkbox", { name: issue, exact: true }).check();
	await apply(page).click();
	await expect.poll(() => attempts.length).toBe(2);
	expect(attempts[0].id).toBeTruthy();
	expect(attempts[0].body.selected_test_case_ids).toEqual([]);
	expect(attempts[1]).toEqual(attempts[0]);
	await expect(page.getByRole("button", { name: "Open QA project menu" })).toContainText(`revision ${project.current_revision + 1}`);
	await page.getByRole("tab", { name: /Generated Test Cases/ }).click();
	await expect(page.getByRole("button", { name: new RegExp(changedCase.title) })).toBeVisible();
});

const mixedGroups = [
	{ input_hash: "group-1", status: "incomplete", design_status: "incomplete", requirement_ids: ["REQ-101"], case_ids: ["TC-001"] },
	{ input_hash: "group-2", status: "unknown", requirement_ids: ["REQ-102"], case_ids: ["TC-002"], failure_reason: "provider_unavailable" },
	{
		input_hash: "group-3",
		status: "incomplete",
		design_status: "assessed_complete",
		execution_status: "blocked",
		requirement_ids: ["REQ-103"],
		case_ids: ["TC-003"],
	},
	{ input_hash: "group-4", status: "assessed_complete", requirement_ids: ["REQ-104"], case_ids: ["TC-004"] },
];
const groupCases = [...cases, { ...cases[0], id: "TC-003", title: "Checkout 3" }, { ...cases[1], id: "TC-004", title: "Checkout 4" }];

for (const width of [1440, 390]) {
	test(
		`separates saved review groups from repair and opens the exact case at ${width}px`,
		{ tag: width === 1440 ? "@p1" : [] },
		async ({ page }, testInfo) => {
			await page.setViewportSize({ width, height: 900 });
			const reason = "Independent review left 1 of 4 groups unverified. Completed groups found design gaps.";
			const { requests } = await open(page, {
				payload: {
					test_cases: groupCases,
					review: { approved: false, score: 95, threshold: 90, blocking_issues: [issue, reason], unmet_criteria: [] },
					substantive_assessment: {
						status: "incomplete",
						design_status: "incomplete",
						reason,
						diagnostics: { unverified_group_count: 1, groups: mixedGroups },
					},
				},
			});
			const groups = page.getByRole("region", { name: "Independent review groups" });
			await expect(groups).toContainText("3 of 4 groups assessed · 1 unverified");
			await expect(panel(page).getByRole("checkbox", { name: reason })).toHaveCount(0);
			await groups.getByText("Show review groups", { exact: true }).focus();
			await page.keyboard.press("Enter");
			for (const label of ["Design gaps", "Unverified", "Execution setup needed", "Assessed"]) {
				await expect(groups.getByText(label, { exact: true })).toBeVisible();
			}
			await expect(groups.getByText("REQ-102 · 1 test case")).toBeVisible();
			await expect(groups.getByText("Review provider unavailable")).toBeVisible();
			const accessibility = await new AxeBuilder({ page }).include(".test-review-groups").analyze();
			expect(accessibility.violations).toEqual([]);
			await groups.screenshot({ path: testInfo.outputPath("review-groups.png") });
			const inspection = groups.getByRole("button", { name: "Inspect group 2 cases" });
			await inspection.focus();
			await page.keyboard.press("Enter");
			const results = page.getByRole("tabpanel", { name: "Generated Test Cases" });
			await expect(results).toBeFocused();
			await expect(results.getByText("Showing cases in Group 2")).toBeVisible();
			await expect(results.getByText("1 of 4 test cases")).toBeVisible();
			await expect(results.getByRole("heading", { name: "Checkout 2" })).toBeVisible();
			await expect(results.getByRole("button", { name: /TC-001 Checkout/ })).toHaveCount(0);
			await results.getByRole("button", { name: "Filters", exact: true }).click();
			const priority = results.getByRole("combobox", { name: "Priority", exact: true });
			await priority.selectOption("Low");
			await expect(results.getByText("0 of 4 test cases")).toBeVisible();
			await results.getByRole("button", { name: "Clear filters", exact: true }).click();
			await expect(results.getByText("1 of 4 test cases")).toBeVisible();
			await priority.selectOption("Medium");
			expect(requests).toEqual([]);
			await results.getByRole("button", { name: "Clear group filter" }).click();
			await expect(results).toBeFocused();
			await results.getByRole("button", { name: "Filters", exact: true }).click();
			await expect(priority).toHaveValue("");
			await expect(results.getByText("4 of 4 test cases")).toBeVisible();
			await page.reload();
			await expect(groups).toContainText("3 of 4 groups assessed · 1 unverified");
			await page.getByRole("tab", { name: /^Improve tests/ }).click();
			await panel(page).getByRole("button", { name: "Select all", exact: true }).click();
			await apply(page).click();
			await expect.poll(() => requests.length).toBe(1);
			expect(requests[0].feedback).toContain(issue);
			expect(requests[0].feedback).not.toContain(reason);
			const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
			expect(overflow).toBeLessThanOrEqual(1);
		}
	);
}

test("a review-only failure shows last attempt groups and keeps review available", { tag: "@p1" }, async ({ page }) => {
	const reason = "Independent assessment provider unavailable; review is required.";
	const { requests } = await open(page, {
		operation: "testcases.generate",
		metadata: { review_retry_available: true },
		useCaseReviewState: "approved",
		payload: {
			test_cases: groupCases,
			review: { approved: false, score: 100, threshold: 90, blocking_issues: [reason], unmet_criteria: [] },
			substantive_assessment: { status: "unknown", reason, diagnostics: { failure_reason: "provider_unavailable" } },
		},
	});
	let attempts = 0;
	await page.route("**/test-cases/review", (route) => {
		attempts++;
		return route.fulfill({
			status: 503,
			json: {
				detail: { code: "review_groups_unverified", message: "Independent review left 1 of 4 groups unverified.", groups: mixedGroups },
			},
		});
	});
	await expect(panel(page).getByRole("heading", { name: "Independent review needed" })).toBeVisible();
	await expect(panel(page).getByRole("checkbox")).toHaveCount(0);
	await expect(panel(page).getByRole("button", { name: /Apply.*(fixes|improvements)/ })).toHaveCount(0);
	const retry = page.getByRole("button", { name: "Review saved suite", exact: true });
	await expect(retry).toBeEnabled();
	await retry.click();
	const latest = page.getByRole("region", { name: "Last review attempt" });
	await expect(latest).toContainText("3 of 4 groups assessed · 1 unverified");
	await latest.getByText("Show review groups", { exact: true }).click();
	await latest.getByRole("button", { name: "Inspect group 2 cases" }).click();
	await expect(page.getByText("Showing cases in Group 2")).toBeVisible();
	expect(attempts).toBe(1);
	expect(requests).toEqual([]);
	await page.reload();
	await expect(latest).toHaveCount(0);
	await expect(retry).toBeEnabled();
});

test("shows the saved browser handoff reason independently of business approval", { tag: "@p1" }, async ({ page }) => {
	const summary = "Browser handoff retained 1 of 1 browser-intended cases for manual review.";
	const reason = "Browser handoff for TC-002 needs manual review. Source step 2 could not be mapped to a complete browser check.";
	await open(page, {
		payload: {
			review: { approved: true, score: 95, threshold: 90 },
			workflow_diagnostics: { warnings: [summary, reason, "Internal workflow warning"] },
		},
	});
	await page.reload();
	await page.getByRole("tab", { name: /^Improve tests/ }).click();
	await expect(panel(page).getByRole("heading", { name: "Machine quality check passed" })).toBeVisible();
	await expect(panel(page).getByRole("heading", { name: "Browser handoff" })).toBeVisible();
	await expect(panel(page).getByText(summary, { exact: true })).toBeVisible();
	await expect(panel(page).getByText(reason, { exact: true })).toBeVisible();
	await expect(panel(page)).not.toContainText("Internal workflow warning");
});

test("a setup-only finding leads to the exact saved prerequisite, with no automatic write", { tag: "@p1" }, async ({ page }) => {
	const reason = "Business test design is assessed. Resolve declared execution prerequisites before approval.";
	const prerequisite = { name: "supervisor_login", kind: "credential", description: "Account with Supervisor role", status: "missing" };
	const { api, project, requests } = await open(page, {
		payload: {
			test_cases: [cases[0], { ...cases[1], expected_result: "Checkout is saved", execution_prerequisites: [prerequisite] }],
			review: { approved: false, score: 100, threshold: 90, summary: reason, blocking_issues: [reason] },
			substantive_assessment: {
				status: "incomplete",
				design_status: "assessed_complete",
				execution_status: "blocked",
				execution_blockers: [{ test_case_id: "TC-002", prerequisite_name: prerequisite.name }],
				reason,
				obligations: [],
				case_grounding: [],
			},
		},
	});
	const captures = [];
	await page.route(`**/projects/${USE_CASE_PROJECT_ID}/quality-findings`, (route) => {
		if (route.request().method() === "POST") captures.push(route.request().postDataJSON());
		return route.fulfill({ json: { project_revision: 7, findings: [] } });
	});
	await page.reload();
	const coverage = page.getByRole("region", { name: "Business coverage assessment" });
	const setup = page.getByRole("region", { name: "Execution setup" });
	await expect(coverage).toContainText("Business coverage: assessed complete");
	await expect(setup).toContainText("TC-002");
	await expect(setup).toContainText(prerequisite.name);
	await expect(panel(page).getByRole("heading", { name: "Execution setup needed" })).toBeVisible();
	await expect(panel(page).getByRole("checkbox", { name: reason, exact: true })).toHaveCount(0);
	await expect(panel(page).getByRole("button", { name: /^Apply / })).toHaveCount(0);
	const navigation = page.getByRole("navigation", { name: "Project navigation" });
	await expect(navigation.getByRole("link", { name: "Test Cases, Setup needed", exact: true })).toBeVisible();
	await navigation.getByRole("link", { name: "Overview", exact: true }).click();
	await expect(page.getByText("Test cases need execution setup", { exact: true })).toBeVisible();
	await page.getByRole("link", { name: "View test cases", exact: true }).click();
	const link = setup.getByRole("link", { name: "Review prerequisite supervisor_login for TC-002" });
	const href = await link.getAttribute("href");
	await link.focus();
	await link.press("Enter");
	const feedback = page.getByRole("region", { name: "Quality feedback" });
	await expect(feedback.getByRole("heading", { name: "Quality feedback" })).toBeFocused();
	await expect(feedback.getByLabel("Evidence snapshot")).toHaveValue("cases-v1");
	await expect(feedback.getByLabel("Finding type")).toHaveValue("access");
	await expect(feedback.getByLabel("Affected test case IDs")).toHaveValue("TC-002");
	await expect(feedback.getByLabel("Missing prerequisite name")).toHaveValue(prerequisite.name);
	await expect(feedback.getByLabel("Prerequisite description")).toHaveValue(prerequisite.description);
	await expect(feedback.getByLabel("Expected behavior")).toHaveValue("Checkout is saved");
	expect(captures).toHaveLength(0);
	expect(requests).toHaveLength(0);
	await feedback.getByRole("button", { name: "Record finding", exact: true }).click();
	await expect.poll(() => captures.length).toBe(1);
	expect(captures[0]).toMatchObject({ source_snapshot_id: "cases-v1", test_case_ids: ["TC-002"], prerequisites: [prerequisite] });
	const mismatched = new URL(href, page.url());
	mismatched.searchParams.set("case", "TC-001");
	await page.goto(mismatched.href);
	await expect(feedback).toContainText("This prerequisite link is no longer current.");
	await expect(feedback.getByRole("button", { name: "Record finding", exact: true })).not.toBeVisible();
	project.current_snapshots.test_cases.snapshot_id = "cases-v2";
	project.stage_state.test_cases.current_snapshot_id = "cases-v2";
	api.setProject(project);
	await page.goto(href);
	await expect(feedback).toContainText("This prerequisite link is no longer current.");
	await expect(feedback.getByRole("button", { name: "Record finding", exact: true })).not.toBeVisible();
	expect(captures).toHaveLength(1);
});

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
	await page.getByRole("tab", { name: /^Generated Test Cases/ }).click();
	const results = page.getByRole("tabpanel", { name: "Generated Test Cases", exact: true });
	await results.getByRole("button", { name: "Filters", exact: true }).click();
	await results.getByRole("combobox", { name: "Priority", exact: true }).selectOption("High");
	await expect(results.getByText("0 of 2 test cases")).toBeVisible();
	await page.getByRole("tab", { name: /^Improve tests/ }).click();
	await panel(page).getByRole("button", { name: "View test case TC-002", exact: true }).click();
	await expect(results).toBeFocused();
	const detail = results.getByRole("region", { name: "Selected test case", exact: true });
	await expect(detail.getByRole("heading", { name: "Checkout 2", exact: true })).toBeVisible();
	await expect(detail.getByText("Opened from Improve tests. This case is outside the current filters.")).toBeVisible();
	await expect(results.getByText("0 of 2 test cases")).toBeVisible();
	await results.getByRole("button", { name: "Clear filters", exact: true }).click();
	await expect(results.getByRole("button", { name: "TC-002 Checkout 2", exact: true })).toHaveAttribute("aria-pressed", "true");
	await page.getByRole("tab", { name: /^Improve tests/ }).click();
	await expect(panel(page).getByRole("checkbox", { name: issue, exact: true })).toBeChecked();
	await expect(panel(page).getByRole("textbox")).toHaveValue("Use clear user-visible outcomes.");
	await expect(panel(page).getByRole("button", { name: /^View test case/ })).toHaveCount(1);
});

test(
	"applies explicit selected feedback with project recommendations, refreshes quality and clears drafts",
	{ tag: [] },
	async ({ page }) => {
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
	}
);

test("failed save preserves drafts and suite; retry succeeds with remaining findings", { tag: "@p1" }, async ({ page }) => {
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

test("unfinished semantic review keeps its score and actionable finding after response and reload", async ({ page }) => {
	const finding = "TC-002 must compare the recorded booking ID after refresh.";
	await open(page, {
		payload: {
			generation_tasks: [{ source_test_case_id: "TC-002", requirement_ids: ["REQ-101"], scenario_refs: [], reason: finding }],
			substantive_assessment: { status: "incomplete", reason: "Source assertion remains unresolved.", obligations: [], case_grounding: [] },
			coverage_metrics: { cases_with_traceability: 2, covered_planned_scenarios: 2, planned_scenarios_total: 3 },
		},
		resultReview: {
			approved: false,
			score: 88,
			threshold: 90,
			summary: "Source assertion remains unresolved.",
			blocking_issues: [finding],
		},
	});
	await panel(page).getByRole("checkbox", { name: issue, exact: true }).check();
	await apply(page).click();
	await expect(panel(page)).toContainText("Quality score 88/100");
	await expect(panel(page).getByRole("checkbox", { name: finding, exact: true })).toBeVisible();
	await page.getByRole("tab", { name: /^Traceability Matrix/ }).click();
	await expect(page.getByRole("tabpanel", { name: "Traceability Matrix", exact: true })).toContainText("Cases with traceability 2/2");
	await expect(page.getByRole("tabpanel", { name: "Traceability Matrix", exact: true })).toContainText("Linked scenarios 2/3");
	await page.reload();
	await expect(panel(page)).toContainText("Quality score 88/100");
	await expect(panel(page).getByRole("checkbox", { name: finding, exact: true })).toBeVisible();
	await expect(page.getByRole("region", { name: "Test Cases readiness" })).toContainText("Draft incomplete");
	await expect(page.getByText("1 generation task(s) remain unresolved.", { exact: true })).toHaveCount(0);
	await page.getByRole("tab", { name: /^Traceability Matrix/ }).click();
	await expect(page.getByRole("tabpanel", { name: "Traceability Matrix", exact: true })).toContainText("Cases with traceability 2/2");
	await expect(page.getByRole("tabpanel", { name: "Traceability Matrix", exact: true })).toContainText("Linked scenarios 2/3");
});

test("stale suite blocks refinement and exposes impact review", async ({ page }) => {
	const { requests } = await open(page, { stale: true });
	await expect(apply(page)).toBeDisabled();
	await expect(panel(page)).toContainText("Upstream inputs changed");
	await panel(page).getByRole("button", { name: "Review impact" }).click();
	await expect(page.getByText("Existing suite needs impact analysis", { exact: true })).toBeVisible();
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
		await expect(panel(page)).toBeVisible();
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

for (const width of [390, 1488]) {
	test(`source obligations distinguish design coverage and prerequisites at ${width}px`, { tag: [] }, async ({ page }) => {
		await page.setViewportSize({ width, height: 1000 });
		await open(page, {
			payload: {
				substantive_assessment: {
					status: "incomplete",
					design_status: "incomplete",
					execution_status: "blocked",
					execution_blockers: [{ test_case_id: "TC-002", prerequisite_name: "fixture_for_supervisor" }],
					reason: "Independent design review; no booking execution.",
					obligations: [
						{
							requirement_id: "ROOM-101",
							source_quote: "whole number from 1 to 8 inclusive",
							obligation: "Create at upper boundary 8",
							status: "unmet",
							reason: "Only entering 8 does not observe creation.",
							evidence: [
								{ test_case_id: "TC-002", step: 1, action: "Enter 8", expected: "Field contains 8" },
								{ test_case_id: "TC-002", step: 2, action: "Click Book", expected: "" },
							],
						},
					],
					case_grounding: [
						{
							test_case_id: "TC-002",
							reason: "Source requirements take precedence over conflicting context.",
							prerequisites: [
								{
									description: "Provision Alice",
									status: "missing_prerequisite",
									required: true,
									reason: "User fixture was not supplied.",
								},
							],
						},
					],
				},
			},
		});
		const region = page.getByRole("region", { name: "Business coverage assessment" });
		await expect(region).toContainText("Business coverage: incomplete");
		await region.getByText("Source obligations and prerequisites", { exact: true }).click();
		await expect(region).toContainText("Create at upper boundary 8 — unmet");
		await expect(region).toContainText("TC-002, step 1: Enter 8 → Field contains 8");
		await expect(region.getByText("TC-002, step 2: Click Book", { exact: true })).toBeVisible();
		await expect(region).toContainText("Provision Alice");
		await expect(page.getByRole("region", { name: "Execution setup" })).toContainText("fixture_for_supervisor");
		await expect(panel(page).getByRole("checkbox", { name: issue, exact: true })).toBeVisible();
		if (width === 1488) await expect(page.getByRole("link", { name: "Test Cases, Needs refinement", exact: true })).toBeVisible();
		await page.reload();
		await expect(region).toContainText("Business coverage: incomplete");
		await region.getByText("Source obligations and prerequisites", { exact: true }).click();
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2)).toBe(true);
		const results = await new AxeBuilder({ page }).analyze();
		expect(results.violations.filter((v) => ["serious", "critical"].includes(v.impact))).toEqual([]);
		await page.screenshot({ path: test.info().outputPath(`business-coverage-${width}.png`), fullPage: true });
	});
}
