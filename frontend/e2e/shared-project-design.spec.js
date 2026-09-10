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

async function openCases(page, testCases = [caseFixture("TC-001", "Valid checkout"), caseFixture("TC-002", "Declined card")]) {
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
		},
	};
	project.stage_state.test_cases = { current_snapshot_id: "cases-v1", approved: false, stale: false, version: 1 };
	await installUseCaseReviewApi(page, { initialProject: project });
	await seedAuthenticatedSession(page);
	await page.goto(`/projects/${USE_CASE_PROJECT_ID}/test-cases`);
	await expect(page.getByRole("heading", { name: "Test Cases", exact: true, level: 1 })).toBeVisible();
	return project;
}

test("selects cases, expands steps and preserves all case metadata while searching", async ({ page }) => {
	await openCases(page);
	const detail = page.getByRole("region", { name: "Selected test case" });
	await expect(detail.getByRole("heading", { name: "Valid checkout" })).toBeVisible();
	await expect(detail).not.toContainText("TC-001 action 3");
	await detail.getByRole("button", { name: "Show all 3 steps" }).click();
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
	await page.getByRole("region", { name: "Test case quality" }).getByText("View findings", { exact: true }).click();
	await expect(page.getByRole("region", { name: "Test case quality" })).toContainText("TC-001 requires a grounded button label.");
	await page.getByRole("button", { name: "Template setup", exact: true }).click();
	await page.getByRole("button", { name: "Generate and review", exact: true }).click();
	await expect(page.getByRole("heading", { name: "Generated Test Cases", exact: true })).toBeVisible();
});

test("requires an explicit human decision and keeps navigation status separate", async ({ page }) => {
	const api = await installUseCaseReviewApi(page);
	await seedAuthenticatedSession(page);
	await page.goto(`/projects/${USE_CASE_PROJECT_ID}/use-cases`);
	const form = page.getByRole("form", { name: "Human review decision" });
	await expect(form.getByRole("radio", { name: /^Approve/ })).not.toBeChecked();
	await expect(form.getByRole("radio", { name: /^Request changes/ })).not.toBeChecked();
	await expect(form.getByRole("button", { name: "Choose a decision" })).toBeDisabled();
	const nav = page.getByRole("navigation", { name: "Project navigation", exact: true });
	await expect(nav.getByRole("link", { name: "Use Cases, Awaiting review" })).toHaveAttribute("aria-current", "page");
	await form.getByRole("radio", { name: /^Request changes/ }).check();
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

test("Use Cases shares resizing while preserving the explicit review decision", async ({ page }) => {
	await page.setViewportSize({ width: 1488, height: 1056 });
	const api = await installUseCaseReviewApi(page);
	await seedAuthenticatedSession(page);
	await page.goto(`/projects/${USE_CASE_PROJECT_ID}/use-cases`);
	const divider = page.getByRole("separator", { name: "Resize scenarios and review decision" });
	await expect(divider).toBeVisible();
	await divider.focus();
	await page.keyboard.press("Home");
	await expect(divider).toHaveAttribute("aria-valuenow", await divider.getAttribute("aria-valuemin"));
	await expect(page.getByRole("radio", { name: "Approve", exact: true })).not.toBeChecked();
	expect(api.requests.review).toEqual([]);
	await page.setViewportSize({ width: 390, height: 844 });
	await expect(divider).toHaveCount(0);
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
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

test("step table aligns numbers, actions and expected results and retains expanded test data", async ({ page }) => {
	await openCases(page);
	const table = page.getByRole("region", { name: "Test steps and expected results" }).getByRole("table");
	await expect(table.getByRole("columnheader")).toHaveText(["Step", "Action", "Expected result"]);
	await expect(table.getByRole("rowheader")).toHaveText(["1", "2"]);
	await expect(table.getByRole("row").nth(1).getByRole("cell")).toHaveText(["TC-001 action 1", "TC-001 expectation 1"]);
	await page.getByRole("button", { name: "Show all 3 steps" }).click();
	await expect(table.getByRole("rowheader")).toHaveText(["1", "2", "3"]);
	await expect(table.getByRole("row").nth(3).getByRole("cell").first()).toContainText("Step-specific data");
	await page.getByRole("button", { name: "Show fewer steps" }).click();
	await expect(table.getByRole("rowheader")).toHaveCount(2);
	await page.setViewportSize({ width: 390, height: 844 });
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
