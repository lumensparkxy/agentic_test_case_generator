import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { installUseCaseReviewApi, useCaseProjectFixture, USE_CASE_PROJECT_ID } from "./support/use-case-review.js";

const caseFixture = (id, title) => ({
	id,
	title,
	description: `Objective for ${title}`,
	priority: "High",
	type: "Functional",
	status: "Draft",
	preconditions: "Signed in",
	expected_result: `Result for ${id}`,
	test_data: "Fixture test data",
	estimated_time: "3 minutes",
	automation_status: "Manual",
	component: "Checkout",
	tags: ["REQ-101"],
	steps: [1, 2, 3].map((n) => ({
		step: n,
		action: `${id} action ${n}`,
		expected: `${id} expectation ${n}`,
		test_data: n === 3 ? "Step-specific data" : "",
	})),
});

async function openCases(
	page,
	testCases = [caseFixture("TC-001", "Valid checkout"), caseFixture("TC-002", "Declined card")],
	payload = {}
) {
	const project = useCaseProjectFixture();
	project.current_snapshots.test_cases = {
		snapshot_id: "cases-v1",
		project_id: USE_CASE_PROJECT_ID,
		stage: "test_cases",
		version: 1,
		project_revision: 7,
		payload: {
			test_cases: testCases,
			review: {
				approved: false,
				score: 65,
				threshold: 90,
				blocking_issues: ["TC-001 requires a grounded button label."],
				summary: "Grounded UI details required.",
			},
			...payload,
		},
	};
	project.stage_state.test_cases = { current_snapshot_id: "cases-v1", approved: false, stale: false, version: 1 };
	await installUseCaseReviewApi(page, { initialProject: project });
	await seedAuthenticatedSession(page);
	await page.goto(`/projects/${USE_CASE_PROJECT_ID}/test-cases`);
	await expect(page.getByRole("heading", { name: "Test Cases", exact: true, level: 1 })).toBeVisible();
	await page.getByRole("tab", { name: /^Generated Test Cases/ }).click();
	return project;
}

test("selects cases and preserves all steps and metadata while searching", async ({ page }) => {
	await openCases(page);
	const detail = page.getByRole("region", { name: "Selected test case" });
	await expect(detail.getByRole("heading", { name: "Valid checkout" })).toBeVisible();
	await expect(detail).toContainText("TC-001 action 3");
	await expect(detail).toContainText("Step-specific data");
	await detail.getByText("Test data and metadata", { exact: true }).click();
	await expect(detail).toContainText("Fixture test data");
	await expect(detail).toContainText("Manual");
	await page.getByRole("button", { name: /TC-002 Declined card/ }).click();
	await expect(detail.getByRole("heading", { name: "Declined card" })).toBeVisible();
	await expect(detail).toContainText("Result for TC-002");
	await expect(detail).not.toContainText("TC-001 action");
	await page.getByRole("searchbox", { name: "Search test cases" }).fill("Valid");
	await expect(detail.getByRole("heading", { name: "Valid checkout" })).toBeVisible();
	await page.getByRole("searchbox", { name: "Search test cases" }).fill("unmatched");
	await expect(page.getByText("No test cases match your search.")).toBeVisible();
	await expect(detail).toContainText("Select a test case");
	await page.getByRole("searchbox", { name: "Search test cases" }).fill("");
	await expect(detail.getByRole("heading", { name: "Declined card" })).toBeVisible();
	await page.getByRole("tab", { name: /^Improve tests/ }).click();
	await expect(page.getByRole("region", { name: "Improve test quality" })).toContainText("TC-001 requires a grounded button label.");
	await page.getByRole("button", { name: "Template setup", exact: true }).click();
	await page.getByRole("button", { name: "Generate and review", exact: true }).click();
	await page.getByRole("tab", { name: /^Generated Test Cases/ }).click();
	await expect(page.getByRole("heading", { name: "Generated Test Cases", exact: true })).toBeVisible();
});

test("requires an explicit human decision and keeps navigation status separate", async ({ page }) => {
	const api = await installUseCaseReviewApi(page);
	await seedAuthenticatedSession(page);
	await page.goto(`/projects/${USE_CASE_PROJECT_ID}/use-cases`);
	const form = page.getByRole("form", { name: "Human review decision" });
	await expect(form).toHaveCount(0);
	const nav = page.getByRole("navigation", { name: "Project navigation", exact: true });
	await expect(nav.getByRole("link", { name: "Use Cases, Awaiting review" })).toHaveAttribute("aria-current", "page");
	await page.getByRole("button", { name: "Request changes", exact: true }).click();
	await form.getByRole("button", { name: "Request changes", exact: true }).click();
	await expect(form.getByRole("alert")).toContainText("Comment required");
	expect(api.requests.review).toHaveLength(0);
});

for (const width of [390, 901, 1488]) {
	test(`test-case review reflows without document overflow at ${width}px`, async ({ page }) => {
		await page.setViewportSize({ width, height: 1056 });
		await openCases(page);
		await expect(page.getByRole("region", { name: "Selected test case" })).toBeVisible();
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
		const input = page.getByRole("searchbox", { name: "Search test cases" });
		await input.fill("Declined");
		await expect(page.getByRole("region", { name: "Selected test case" })).toContainText("Declined card");
	});
}

test("shared template fields expose labels, help text and controlled values", async ({ page }) => {
	await openCases(page);
	await page.getByRole("button", { name: "Template setup", exact: true }).click();
	const name = page.getByRole("textbox", { name: "Template name", exact: true });
	await name.fill("Regression suite");
	await expect(name).toHaveValue("Regression suite");
	const format = page.getByRole("combobox", { name: "Template format", exact: true });
	await expect(format).toHaveAttribute("aria-describedby", /hint$/);
	await format.focus();
	await expect(format).toBeFocused();
	await page.getByRole("button", { name: "Generate and review", exact: true }).click();
	await expect(page.getByRole("region", { name: "Selected test case" })).toBeVisible();
});

test("shared result tabs support arrow navigation and a keyboard-scrollable table", async ({ page }) => {
	await openCases(page);
	const casesTab = page.getByRole("tab", { name: /^Generated Test Cases/ });
	await casesTab.focus();
	await page.keyboard.press("ArrowRight");
	const traceability = page.getByRole("tab", { name: /^Traceability Matrix/ });
	await expect(traceability).toBeFocused();
	await expect(traceability).toHaveAttribute("aria-selected", "true");
	await expect(casesTab).toHaveAttribute("tabindex", "-1");
	const table = page.getByRole("region", { name: "Requirement traceability table" });
	await table.focus();
	await expect(table).toBeFocused();
	await expect(table.getByRole("columnheader", { name: "Requirement", exact: true })).toHaveAttribute("scope", "col");
});

test("shared settings dialog traps focus, labels integration fields and restores its trigger", async ({ page }) => {
	await openCases(page);
	const trigger = page.getByRole("button", { name: "Open settings", exact: true });
	await trigger.click();
	const dialog = page.getByRole("dialog", { name: "Settings", exact: true });
	const close = dialog.getByRole("button", { name: "Close settings dialog", exact: true });
	await expect(close).toBeFocused();
	await expect(dialog.getByRole("spinbutton", { name: "Approval threshold", exact: true })).toHaveCount(2);
	await page.keyboard.press("Shift+Tab");
	await expect.poll(() => dialog.evaluate((element) => element.contains(document.activeElement))).toBe(true);
	await dialog.getByRole("tab", { name: "Workflow tuning", exact: true }).focus();
	await page.keyboard.press("ArrowRight");
	await expect(dialog.getByRole("tab", { name: "Integrations", exact: true })).toHaveAttribute("aria-selected", "true");
	await expect(dialog.getByRole("textbox", { name: "JIRA base URL", exact: true })).toBeVisible();
	await page.keyboard.press("Escape");
	await expect(dialog).toHaveCount(0);
	await expect(trigger).toBeFocused();
});

test("project Back and Next follow sidebar order without approving or generating", async ({ page }) => {
	await openCases(page);
	const writes = [];
	page.on("request", (request) => {
		if (request.method() === "POST") writes.push(request.url());
	});
	await page
		.getByRole("navigation", { name: "Project navigation", exact: true })
		.getByRole("link", { name: /^Requirements,/ })
		.click();
	for (const destination of ["context", "use-cases", "test-cases", "automation", "reports"]) {
		await page.getByRole("button", { name: "Next", exact: true }).click();
		await expect(page).toHaveURL(`/projects/${USE_CASE_PROJECT_ID}/${destination}`);
		await expect(page.getByRole("heading", { name: "Template Setup", exact: true })).toHaveCount(0);
	}
	for (const destination of ["automation", "test-cases", "use-cases", "context", "requirements"]) {
		await page.getByRole("button", { name: "Back", exact: true }).click();
		await expect(page).toHaveURL(`/projects/${USE_CASE_PROJECT_ID}/${destination}`);
	}
	expect(writes).toEqual([]);
});

test("missing Use Cases still provides sequential navigation", async ({ page }) => {
	const project = useCaseProjectFixture();
	delete project.current_snapshots.use_cases;
	delete project.stage_state.use_cases;
	const api = await installUseCaseReviewApi(page, { initialProject: project });
	await seedAuthenticatedSession(page);
	await page.goto(`/projects/${USE_CASE_PROJECT_ID}/context`);
	await page.getByRole("button", { name: "Next", exact: true }).click();
	await expect(page.getByRole("heading", { name: "No Use Cases snapshot", exact: true })).toBeVisible();
	const steps = page.getByRole("navigation", { name: "Workflow steps", exact: true });
	await steps.getByRole("button", { name: "Back", exact: true }).click();
	await expect(page).toHaveURL(`/projects/${USE_CASE_PROJECT_ID}/context`);
	await page.getByRole("button", { name: "Next", exact: true }).click();
	await steps.getByRole("button", { name: "Next", exact: true }).click();
	await expect(page).toHaveURL(`/projects/${USE_CASE_PROJECT_ID}/test-cases`);
	await expect(page.getByRole("button", { name: "Template setup", exact: true })).toBeVisible();
	expect(api.requests.review).toEqual([]);
});

test("template setup is explicit and returns to the normal workbench across navigation and reload", async ({ page }) => {
	await openCases(page);
	const setup = page.getByRole("button", { name: "Template setup", exact: true });
	await setup.click();
	await page.getByRole("textbox", { name: "Template name", exact: true }).fill("Navigation regression");
	await page.getByRole("button", { name: "Back to Test Cases", exact: true }).click();
	await expect(page.getByRole("region", { name: "Selected test case", exact: true })).toBeVisible();
	await setup.click();
	await expect(page.getByRole("textbox", { name: "Template name", exact: true })).toHaveValue("Navigation regression");
	await page
		.getByRole("navigation", { name: "Project navigation", exact: true })
		.getByRole("link", { name: /^Context,/ })
		.click();
	await page.goBack();
	await expect(setup).toBeVisible();
	await expect(page.getByRole("heading", { name: "Template Setup", exact: true })).toHaveCount(0);
	await setup.click();
	await page.reload();
	await page.getByRole("tab", { name: /^Generated Test Cases/ }).click();
	await expect(setup).toBeVisible();
	await expect(page.getByRole("region", { name: "Selected test case", exact: true })).toBeVisible();
});

test("pane divider supports drag, limits, reset and saved widths without changing selection", async ({ page }) => {
	await page.setViewportSize({ width: 1488, height: 1056 });
	await openCases(page);
	const divider = page.getByRole("separator", { name: "Resize test case list and details" });
	await expect(divider).toBeVisible();
	const start = Number(await divider.getAttribute("aria-valuenow"));
	const box = await divider.boundingBox();
	await page.mouse.move(box.x + box.width / 2, box.y + 30);
	await page.mouse.down();
	await page.mouse.move(box.x + 110, box.y + 30, { steps: 10 });
	await page.mouse.up();
	expect(Number(await divider.getAttribute("aria-valuenow"))).toBeGreaterThan(start);
	const saved = await divider.getAttribute("aria-valuenow");
	await page.reload();
	await page.getByRole("tab", { name: /^Generated Test Cases/ }).click();
	await expect(divider).toHaveAttribute("aria-valuenow", saved);
	await expect(page.getByRole("region", { name: "Selected test case" })).toContainText("Valid checkout");
	await divider.focus();
	await page.keyboard.press("Home");
	await expect(divider).toHaveAttribute("aria-valuenow", await divider.getAttribute("aria-valuemin"));
	await page.keyboard.press("End");
	await expect(divider).toHaveAttribute("aria-valuenow", await divider.getAttribute("aria-valuemax"));
	await page.keyboard.press("Enter");
	await expect(divider).toHaveAttribute("aria-valuenow", String(start));
	await page.keyboard.press("ArrowRight");
	await expect(divider).toHaveAttribute("aria-valuenow", String(start + 2));
	await divider.dblclick();
	await expect(divider).toHaveAttribute("aria-valuenow", String(start));
	await page.setViewportSize({ width: 390, height: 844 });
	await expect(divider).toHaveCount(0);
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
	await expect(page.getByRole("region", { name: "Selected test case" })).toBeVisible();
});

test("Use Cases reviews the full snapshot in a cancellable responsive dialog", async ({ page }) => {
	await page.setViewportSize({ width: 1488, height: 1056 });
	const api = await installUseCaseReviewApi(page);
	await seedAuthenticatedSession(page);
	await page.goto(`/projects/${USE_CASE_PROJECT_ID}/use-cases`);
	await expect(page.getByRole("separator", { name: "Resize scenarios and review decision" })).toHaveCount(0);
	await page.getByRole("searchbox", { name: "Search use cases" }).fill("no matching scenarios");
	const approve = page.getByRole("button", { name: "Approve all", exact: true });
	for (const width of [1488, 390]) {
		await page.setViewportSize({ width, height: 844 });
		await approve.click();
		const dialog = page.getByRole("dialog", { name: "Review all Use Cases" });
		await expect(dialog).toContainText("including scenarios hidden by search");
		expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
		const accessibility = await new AxeBuilder({ page }).include('[role="dialog"]').analyze();
		expect(accessibility.violations).toEqual([]);
		await expect(dialog.getByRole("textbox", { name: /^Review comment/ })).toBeFocused();
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
		await page.keyboard.press("Escape");
		await expect(dialog).toHaveCount(0);
		await expect(approve).toBeFocused();
	}
	expect(api.requests.review).toEqual([]);
});

test("project typography uses the compact shared reading scale", async ({ page }) => {
	await openCases(page);
	await expect(page.locator("body")).toHaveCSS("font-size", "14px");
	await expect(page.getByRole("heading", { name: "Test Cases", exact: true, level: 1 })).toHaveCSS("font-size", "28px");
	await expect(page.getByRole("button", { name: "Template setup", exact: true })).toHaveCSS("min-height", "44px");
});

test("case summaries flow inline and wrap only when the pane narrows", async ({ page }) => {
	await page.setViewportSize({ width: 1920, height: 1080 });
	const title = "Valid checkout with a saved payment method and delivery address";
	await openCases(page, [caseFixture("TC-001", title)]);
	const row = page.getByRole("button", { name: /TC-001 Valid checkout/ });
	const text = row.locator(":scope > span");
	const wideHeight = await text.evaluate((element) => element.getBoundingClientRect().height);
	const lineHeight = await text.evaluate((element) => parseFloat(getComputedStyle(element).lineHeight));
	expect(wideHeight).toBeLessThanOrEqual(lineHeight + 1);
	const divider = page.getByRole("separator", { name: "Resize test case list and details" });
	await divider.focus();
	await page.keyboard.press("Home");
	await expect.poll(() => text.evaluate((element) => element.getBoundingClientRect().height)).toBeGreaterThan(wideHeight);
	await expect(row).toHaveAttribute("aria-pressed", "true");
	await expect(row).toHaveText(`TC-001 ${title}`);
	await expect(page.getByRole("region", { name: "Selected test case" })).toContainText("High priority · REQ-101");
});

test("step table aligns numbers, actions and expected results and shows every step and its test data by default", async ({ page }) => {
	await openCases(page);
	const table = page.getByRole("region", { name: "Test steps and expected results" }).getByRole("table");
	await expect(table.getByRole("columnheader")).toHaveText(["Step", "Action", "Expected result"]);
	await expect(table.getByRole("row").nth(1).getByRole("cell")).toHaveText(["TC-001 action 1", "TC-001 expectation 1"]);
	await expect(table.getByRole("rowheader")).toHaveText(["1", "2", "3"]);
	await expect(page.getByRole("button", { name: /Show (all .*|fewer) steps/ })).toHaveCount(0);
	await expect(table.getByRole("row").nth(3).getByRole("cell").first()).toContainText("Step-specific data");
	await page.setViewportSize({ width: 390, height: 844 });
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("stale Use Cases cannot be approved from either review entry point", async ({ page }) => {
	const project = useCaseProjectFixture();
	project.stage_state.use_cases.stale = true;
	project.stage_state.use_cases.stale_reason = "Requirements changed.";
	const api = await installUseCaseReviewApi(page, { initialProject: project });
	await seedAuthenticatedSession(page);
	await page.goto(`/projects/${USE_CASE_PROJECT_ID}/use-cases`);
	await expect(page.getByRole("button", { name: "Approve all", exact: true })).toBeDisabled();
	await page.getByRole("button", { name: "Request changes", exact: true }).click();
	const dialog = page.getByRole("dialog", { name: "Review all Use Cases" });
	await expect(dialog.getByRole("radio", { name: /^Approve/ })).toBeDisabled();
	await expect(dialog).toContainText("Requirements changed.");
	await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
	expect(api.requests.review).toEqual([]);
});

test("Test Cases keeps three result tabs and compact traceability rows", async ({ page }) => {
	const project = await openCases(page);
	const tabs = page.getByRole("tablist", { name: "Generation result sections" });
	await expect(tabs.getByRole("tab")).toHaveCount(3);
	await expect(tabs).not.toContainText("Scenario Coverage");
	await expect(tabs).not.toContainText("Requirement Analysis");
	await expect(tabs).not.toContainText(/Diagnostics|Generation Summary/);
	await tabs.getByRole("tab", { name: /^Traceability Matrix/ }).click();
	const table = page.getByRole("region", { name: "Requirement traceability table" }).getByRole("table");
	await expect(table.getByRole("columnheader")).toHaveText(["Requirement", "Linked test cases", "Scenario coverage", "Status"]);
	for (const [index, requirement] of project.current_snapshots.requirements.payload.requirements.entries()) {
		await expect(
			table
				.getByRole("row")
				.nth(index + 1)
				.getByRole("cell")
				.first()
		).toHaveText(requirement.id);
		await expect(table).not.toContainText(requirement.text);
	}
	await expect(table.getByRole("row").nth(1)).toContainText("TC-001");
	await expect(table.getByRole("row").nth(1)).toContainText("Covered");
	await expect(table.getByRole("row").nth(2)).toContainText("No linked tests");
	await expect(table.getByRole("row").nth(2)).toContainText("Gap");
	for (const width of [1488, 390]) {
		await page.setViewportSize({ width, height: 900 });
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
	}
	await tabs.getByRole("tab", { name: /^Traceability Matrix/ }).focus();
	await page.keyboard.press("ArrowRight");
	await expect(tabs.getByRole("tab", { name: /^Improve tests/ })).toHaveAttribute("aria-selected", "true");
	await page.keyboard.press("ArrowRight");
	await expect(tabs.getByRole("tab", { name: /^Generated Test Cases/ })).toHaveAttribute("aria-selected", "true");
	await expect(page.getByRole("region", { name: "Selected test case" })).toBeVisible();
});

test("coverage gaps select the remaining traceability tab after load and reload", async ({ page }) => {
	await openCases(page, [caseFixture("TC-001", "Valid checkout")], {
		review: { approved: true },
		coverage_metrics: { missing_must_have_scenarios: ["negative"], missing_scenarios: ["boundary"], requirements_without_tests: [] },
	});
	const selected = page.getByRole("tab", { name: /^Traceability Matrix/ });
	await page.reload();
	await expect(selected).toHaveAttribute("aria-selected", "true");
	await expect(page.getByRole("region", { name: "Requirement traceability table" })).toBeVisible();
	await page.reload();
	await expect(selected).toHaveAttribute("aria-selected", "true");
});

test("loaded project without test cases selects a visible results tab", async ({ page }) => {
	await openCases(page, []);
	await expect(page.getByRole("tab", { name: /^Generated Test Cases/ })).toHaveAttribute("aria-selected", "true");
	await expect(page.getByRole("tabpanel", { name: "Generated Test Cases", exact: true })).not.toBeEmpty();
});

for (const state of ["partial", "failed", "completed"]) {
	for (const count of [0, 1])
		test(`result tabs remain usable for ${state} runs with ${count} cases`, async ({ page }) => {
			await openCases(page, count ? [caseFixture("TC-001", "Checkout")] : [], {
				workflow_diagnostics: {
					status: state,
					timed_out: state === "partial",
					failure_reason: state === "failed" ? "internal_error" : null,
					warnings: ["technical warning"],
					parser_failures: ["internal parser message"],
				},
			});
			const tabs = page.getByRole("tablist", { name: "Generation result sections" });
			const panel = page.getByRole("tabpanel");
			await expect(tabs.getByRole("tab")).toHaveCount(3);
			await expect(tabs.getByRole("tab", { name: /^Generated Test Cases/ })).toHaveAttribute("aria-selected", "true");
			await expect(panel).not.toBeEmpty();
			await expect(panel).not.toContainText(/technical warning|internal parser message|Generation Summary|Diagnostics/);
			await page.reload();
			await expect(tabs.getByRole("tab", { name: /^Improve tests/ })).toHaveAttribute("aria-selected", "true");
			await expect(panel).not.toBeEmpty();
		});
}
