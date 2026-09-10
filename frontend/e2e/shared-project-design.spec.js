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
