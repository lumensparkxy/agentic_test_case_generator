import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { seedAuthenticatedSession } from "./support/auth.js";
import { installUseCaseReviewApi, useCaseProjectFixture, USE_CASE_PROJECT_ID } from "./support/use-case-review.js";

async function fixture(page, count = 50, project = useCaseProjectFixture()) {
	project.current_snapshots.test_cases = {
		snapshot_id: "guided-tests-v1",
		stage: "test_cases",
		version: 1,
		payload: {
			test_cases: Array.from({ length: count }, (_, i) => ({
				id: `TC-${i + 1}`,
				title: `Checkout ${i + 1}`,
				linked_requirement_ids: ["REQ-101"],
				steps: [{ step: 1, action: "Submit order", expected: `Receipt ${i + 1} appears` }],
			})),
		},
	};
	project.stage_state.test_cases = { current_snapshot_id: "guided-tests-v1", stale: false, approved: false };
	const api = await installUseCaseReviewApi(page, { initialProject: project });
	await seedAuthenticatedSession(page);
	return { project, api };
}
const path = (stage) => `/projects/${USE_CASE_PROJECT_ID}/${stage}`;

async function openCaseReport(page, number) {
	await page.getByRole("tab", { name: /^Generated Test Cases/ }).press("Enter");
	await page.getByRole("button", { name: `TC-${number} Checkout ${number}`, exact: true }).press("Enter");
	await page.getByRole("button", { name: "Report a problem", exact: true }).press("Enter");
}

for (const width of [1366, 1440, 1920])
	test(
		`desktop recovery preserves selection, search and list scroll at ${width}`,
		{ tag: width === 1440 ? "@p1" : [] },
		async ({ page }) => {
			await page.setViewportSize({ width, height: 900 });
			await fixture(page, width === 1440 ? 200 : 50);
			await page.goto(path("test-cases"));
			await page.getByRole("tab", { name: /Generated Test Cases/ }).click();
			const search = page.getByRole("searchbox", { name: "Search test cases" });
			await search.fill("Checkout");
			await page.getByRole("button", { name: "TC-40 Checkout 40", exact: true }).click();
			const list = page.locator(".test-review-items");
			const scroll = await list.evaluate((e) => e.scrollTop);
			expect(scroll).toBeGreaterThan(0);
			await page.goto(path("use-cases"));
			await page.goto(path("test-cases"));
			await page.getByRole("tab", { name: /Generated Test Cases/ }).click();
			await expect(search).toHaveValue("Checkout");
			await expect(
				page.getByRole("region", { name: "Selected test case" }).getByRole("heading", { name: "Checkout 40", exact: true })
			).toBeVisible();
			await expect.poll(() => list.evaluate((e) => e.scrollTop)).toBeCloseTo(scroll, 0);
			await page.reload();
			await page.getByRole("tab", { name: /Generated Test Cases/ }).click();
			await expect(search).toHaveValue("Checkout");
			await expect(page.getByRole("button", { name: "TC-40 Checkout 40", exact: true })).toHaveAttribute("aria-pressed", "true");
			await search.focus();
			await page.keyboard.press("Tab");
			await expect(page.getByRole("button", { name: "Filters", exact: true })).toBeFocused();
			await page.keyboard.press("Enter");
			await page.keyboard.press("Tab");
			await expect(page.getByRole("combobox", { name: "Priority", exact: true })).toBeFocused();
			await page.keyboard.press("Tab");
			await expect(page.getByRole("combobox", { name: "Automation intent", exact: true })).toBeFocused();
			await page.keyboard.press("Tab");
			await expect(page.getByRole("button", { name: "Go to selected case", exact: true })).toBeFocused();
			await page.keyboard.press("Tab");
			await expect(page.locator(":focus")).toHaveAttribute("data-ui", "selectable-item");
			expect(
				(await new AxeBuilder({ page }).include(".test-review-workspace").withTags(["wcag2a", "wcag2aa"]).analyze()).violations
			).toEqual([]);
			if (width === 1440) {
				await page.evaluate(() => {
					document.documentElement.style.zoom = "2";
				});
				expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
			}
			await page.screenshot({ path: test.info().outputPath(`guided-${width}.png`), fullPage: true });
		}
	);

test("review edits and filters recover; changed source cannot inherit draft approvals", { tag: "@p1" }, async ({ page }) => {
	const { project, api } = await fixture(page);
	await page.goto(path("use-cases"));
	const status = page.getByRole("combobox", { name: "Review status for REQ-101-SCN-01", exact: true });
	await status.selectOption("approved");
	await page.getByRole("combobox", { name: "Filter by review status", exact: true }).selectOption("approved");
	await page.reload();
	await expect(status).toHaveValue("approved");
	await expect(page.getByRole("combobox", { name: "Filter by review status", exact: true })).toHaveValue("approved");
	expect(api.requests.review).toHaveLength(0);
	project.current_revision++;
	project.current_snapshots.use_cases.snapshot_id = "new-use-case-source";
	project.stage_state.use_cases.current_snapshot_id = "new-use-case-source";
	api.setProject(project);
	await page.reload();
	await expect(status).toHaveValue("needs_review");
	await page.getByText("Previous source draft kept for reference", { exact: true }).click();
	await expect(page.getByText("The source changed.", { exact: false })).toBeVisible();
	expect(api.requests.review).toHaveLength(0);
});

test("local storage failure is visible and never claims a saved review", async ({ page }) => {
	await fixture(page);
	await page.addInitScript(() => {
		const set = Storage.prototype.setItem;
		Storage.prototype.setItem = function (k, v) {
			if (k.startsWith("tcg.draft")) throw new DOMException("Full", "QuotaExceededError");
			return set.call(this, k, v);
		};
	});
	await page.goto(path("use-cases"));
	await page.getByRole("combobox", { name: "Review status for REQ-101-SCN-01", exact: true }).selectOption("approved");
	await expect(page.getByRole("alert")).toContainText("Local recovery is unavailable");
	await expect(page.getByText(/1 unsaved reviews/)).toBeVisible();
});

test(
	"selected-test report prefills evidence, preserves instructions through failure and submits scoped repair",
	{ tag: "@p1" },
	async ({ page }, testInfo) => {
		const { project, api } = await fixture(page);
		project.current_snapshots.test_cases.payload.test_cases[1].steps = [
			{ step: 4, action: "Submit order", expected: "Receipt 2 appears" },
			{ step: 7, action: "Open the saved receipt", expected: "" },
			{ step: 9, action: "Read the receipt identifier", expected: "The original identifier is displayed" },
		];
		api.setProject(project);
		const savedEvidence = [
			"Test TC-2: Checkout 2",
			"Step 4: Submit order | Expected: Receipt 2 appears",
			"Step 7: Open the saved receipt | Expected: Not specified",
			"Step 9: Read the receipt identifier | Expected: The original identifier is displayed",
			"Overall expected: Not specified",
		].join("\n");
		let attempt = 0;
		const bodies = [];
		await page.route("**/testcases/refine", async (route) => {
			bodies.push(route.request().postDataJSON());
			attempt++;
			if (attempt === 1) return route.fulfill({ status: 503, json: { detail: "Temporarily unavailable" } });
			const payload = { ...project.current_snapshots.test_cases.payload, scoped_refinement: { changed: 1, added: 0, preserved: 49 } };
			project.current_snapshots.test_cases = { ...project.current_snapshots.test_cases, snapshot_id: "repaired-tests", payload };
			project.current_revision++;
			api.setProject(project);
			await route.fulfill({ json: payload });
		});
		await page.goto(path("test-cases"));
		await page.getByRole("tab", { name: /Generated Test Cases/ }).click();
		await page.getByRole("button", { name: "TC-2 Checkout 2", exact: true }).click();
		const reader = page.getByRole("region", { name: "Selected test case", exact: true });
		await expect(reader.getByRole("rowheader")).toHaveText(["4", "7", "9"]);
		await reader.getByRole("button", { name: "Report a problem", exact: true }).press("Enter");
		const panel = page.getByRole("region", { name: "Improve test quality" });
		const report = panel.getByRole("region", { name: "Report a problem · TC-2", exact: true });
		const evidence = report.locator("pre");
		const instructions = report.getByRole("textbox", { name: "What is wrong with this test?", exact: true });
		const repair = report.getByRole("button", { name: "Repair selected test", exact: true });
		await expect(evidence).toBeVisible();
		expect(await evidence.textContent()).toBe(savedEvidence);
		await expect(page.getByRole("tabpanel", { name: "Improve tests", exact: true })).toBeFocused();
		await page.keyboard.press("Tab");
		await expect(instructions).toBeFocused();
		await expect(report.getByText("0 selected", { exact: true })).toHaveCount(0);
		await instructions.fill("Check the receipt identifier explicitly.");
		await report.screenshot({ path: testInfo.outputPath("report-saved-steps.png") });
		await repair.click();
		await expect(report.getByRole("alert")).toContainText("Temporarily unavailable");
		await page.reload();
		await page.getByRole("tab", { name: /Improve tests/ }).click();
		await expect(instructions).toHaveValue("Check the receipt identifier explicitly.");
		expect(await evidence.textContent()).toBe(savedEvidence);
		await repair.click();
		await expect(panel).toContainText("1 changed · 0 added · 49 preserved");
		expect(bodies).toHaveLength(2);
		for (const body of bodies) {
			expect(body.selected_test_case_ids).toEqual(["TC-2"]);
			expect(body.feedback).toContain(`${savedEvidence}\nReported problem: Check the receipt identifier explicitly.`);
		}
	}
);

test("recovers a dropped selected repair response with the same request identity", { tag: "@p1" }, async ({ page }) => {
	const { project, api } = await fixture(page, 2);
	const attempts = [];
	const original = project.current_snapshots.test_cases;
	const payload = {
		...original.payload,
		test_cases: [original.payload.test_cases[0], { ...original.payload.test_cases[1], title: "Verified checkout receipt" }],
		scoped_refinement: { changed: 1, added: 0, preserved: 1, changed_ids: ["TC-2"] },
	};
	await page.route("**/testcases/refine", async (route) => {
		const request = route.request();
		attempts.push({ id: request.headers()["x-request-id"], body: request.postDataJSON() });
		if (attempts.length === 1) {
			project.current_snapshots.test_cases = { ...original, snapshot_id: "repair-receipt", payload };
			project.stage_state.test_cases.current_snapshot_id = "repair-receipt";
			project.current_revision++;
			api.setProject(project);
			return route.abort("failed");
		}
		await route.fulfill({ json: payload });
	});
	await page.goto(path("test-cases"));
	await page.getByRole("tab", { name: /Generated Test Cases/ }).click();
	await page.getByRole("button", { name: "TC-2 Checkout 2", exact: true }).click();
	await page.getByRole("button", { name: "Report a problem", exact: true }).click();
	const panel = page.getByRole("region", { name: "Improve test quality" });
	await panel.getByLabel("What is wrong with this test?").fill("Assert the exact saved receipt.");
	await panel.getByRole("button", { name: "Repair selected test", exact: true }).click();
	await expect(panel).toContainText("1 changed · 0 added · 1 preserved");
	expect(attempts).toHaveLength(2);
	expect(attempts[0].id).toBeTruthy();
	expect(attempts[0].body.selected_test_case_ids).toEqual(["TC-2"]);
	expect(attempts[1]).toEqual(attempts[0]);
});

test(
	"keeps case report notes separate from suite improvements across navigation and reload",
	{ tag: "@p1" },
	async ({ page }, testInfo) => {
		const { project, api } = await fixture(page, 2);
		const originalSuite = project.current_snapshots.test_cases;
		const firstCaseNote = "Check the original receipt on TC-1 only.";
		const secondCaseNote = "Check the rejection on TC-2 only.";
		const suiteNote = "Use consistent wording across the saved suite.";
		await page.addInitScript(
			({ projectId, snapshotId, instructions }) => {
				const key = `tcg.draft.v1:${JSON.stringify(["playwright-e2e-user", projectId, "test-improvements"])}`;
				if (!localStorage.getItem(key)) {
					localStorage.setItem(key, JSON.stringify({ snapshotId, value: { selected: [], instructions, caseId: "TC-1" } }));
				}
			},
			{ projectId: project.project_id, snapshotId: `${project.project_id}:${originalSuite.snapshot_id}`, instructions: firstCaseNote }
		);
		const requests = [];
		await page.route("**/testcases/refine", (route) => {
			requests.push(route.request().postDataJSON());
			const payload = { ...originalSuite.payload, scoped_refinement: { changed: 1, added: 0, preserved: 1 } };
			const snapshotId = "case-draft-scope-successor";
			project.current_snapshots.test_cases = { ...originalSuite, snapshot_id: snapshotId, payload };
			project.stage_state.test_cases.current_snapshot_id = snapshotId;
			project.current_revision++;
			api.setProject(project);
			return route.fulfill({ json: payload });
		});
		await page.goto(path("test-cases"));
		const panel = page.getByRole("region", { name: "Improve test quality" });
		const caseInstructions = panel.getByRole("textbox", { name: "What is wrong with this test?", exact: true });
		const suiteInstructions = panel.getByRole("textbox", { name: "Additional instructions (optional)", exact: true });
		const suiteApply = panel.getByRole("button", { name: "Apply improvements", exact: true });
		const returnToSuite = panel.getByRole("button", { name: "Return to suite improvements", exact: true });
		const improve = page.getByRole("tab", { name: /^Improve tests/ });
		await improve.press("Enter");
		await expect(caseInstructions).toHaveValue(firstCaseNote);
		await returnToSuite.press("Enter");
		await expect(suiteInstructions).toHaveValue("", { timeout: 5000 });
		await expect(suiteApply).toBeDisabled();
		await suiteInstructions.scrollIntoViewIfNeeded();
		await page.screenshot({ path: testInfo.outputPath("empty-suite-draft.png") });
		await suiteInstructions.fill(suiteNote);
		await openCaseReport(page, 2);
		await expect(caseInstructions).toHaveValue("");
		await caseInstructions.fill(secondCaseNote);
		await returnToSuite.press("Enter");
		await expect(suiteInstructions).toHaveValue(suiteNote);
		await page.reload();
		await improve.press("Enter");
		await expect(suiteInstructions).toHaveValue(suiteNote);
		await openCaseReport(page, 1);
		await expect(caseInstructions).toHaveValue(firstCaseNote);
		await openCaseReport(page, 2);
		await expect(caseInstructions).toHaveValue(secondCaseNote);
		expect(requests).toEqual([]);
		await panel.getByRole("button", { name: "Repair selected test", exact: true }).press("Enter");
		await expect(panel).toContainText("1 changed · 0 added · 1 preserved");
		expect(requests).toHaveLength(1);
		expect(requests[0].selected_test_case_ids).toEqual(["TC-2"]);
		expect(requests[0].feedback).toContain(secondCaseNote);
		expect(requests[0].feedback).not.toContain(firstCaseNote);
		expect(requests[0].feedback).not.toContain(suiteNote);
		await expect(suiteInstructions).toHaveValue("");
		await expect(suiteApply).toBeDisabled();
		await page.reload();
		await improve.press("Enter");
		await panel.getByText("Previous source draft kept for reference", { exact: true }).press("Enter");
		await expect(panel.getByText(suiteNote, { exact: false })).toBeVisible();
		await expect(panel.getByText(firstCaseNote, { exact: false })).toBeVisible();
		await expect(panel.getByText(secondCaseNote, { exact: false })).toHaveCount(0);
	}
);

test("keeps repair failures with their submitted draft when the reviewer switches reports", { tag: "@p1" }, async ({ page }, testInfo) => {
	const { project, api } = await fixture(page, 2);
	const firstNote = "Assert the existing receipt for TC-1.";
	const secondNote = "Check the denial banner in TC-2.";
	const suiteNote = "Use consistent wording across the suite.";
	const requests = [];
	let releaseFailure;
	const pendingFailure = new Promise((resolve) => {
		releaseFailure = resolve;
	});
	await page.route("**/testcases/refine", async (route) => {
		requests.push(route.request().postDataJSON());
		if (requests.length === 1) {
			await pendingFailure;
			return route.fulfill({ status: 503, json: { detail: "Temporary failure for TC-1" } });
		}
		project.current_revision++;
		api.setProject(project);
		return route.fulfill({ status: 409, json: { detail: "Project revision changed" } });
	});
	await page.goto(path("test-cases"));
	await page.getByRole("tab", { name: /^Improve tests/ }).press("Enter");
	const panel = page.getByRole("region", { name: "Improve test quality" });
	const instructions = panel.getByRole("textbox", { name: "What is wrong with this test?", exact: true });
	const suiteInstructions = panel.getByRole("textbox", { name: "Additional instructions (optional)", exact: true });
	const repair = panel.getByRole("button", { name: "Repair selected test", exact: true });
	await suiteInstructions.fill(suiteNote);
	await openCaseReport(page, 2);
	await instructions.fill(secondNote);
	await openCaseReport(page, 1);
	await instructions.fill(firstNote);
	await repair.press("Enter");
	await expect.poll(() => requests.length).toBe(1);
	try {
		await openCaseReport(page, 2);
		await expect(instructions).toHaveValue(secondNote);
		await expect(instructions).toBeDisabled();
	} finally {
		releaseFailure();
	}
	await expect(repair).toBeEnabled();
	await expect.soft(panel.getByRole("alert")).toHaveCount(0, { timeout: 5000 });
	await openCaseReport(page, 1);
	await expect(panel.getByRole("alert")).toContainText("Temporary failure for TC-1");
	await expect(instructions).toHaveValue(firstNote);
	await repair.press("Enter");
	await expect(panel.getByRole("alert")).toContainText("The project changed");
	await openCaseReport(page, 2);
	await expect.soft(panel.getByRole("alert")).toContainText("TC-1", { timeout: 5000 });
	await expect(instructions).toHaveValue(secondNote);
	await expect(repair).toBeDisabled();
	await panel
		.getByRole("region", { name: "Report a problem · TC-2" })
		.screenshot({ path: testInfo.outputPath("scoped-revision-conflict.png") });
	await panel.getByRole("button", { name: "Reload current findings", exact: true }).press("Enter");
	await expect(panel.getByRole("alert")).toHaveCount(0);
	await expect(instructions).toHaveValue(secondNote, { timeout: 5000 });
	await expect(repair).toBeEnabled();
	await openCaseReport(page, 1);
	await expect(instructions).toHaveValue("");
	await expect(repair).toBeDisabled();
	await panel.getByRole("button", { name: "Return to suite improvements", exact: true }).press("Enter");
	await expect(suiteInstructions).toHaveValue(suiteNote);
	expect(requests).toHaveLength(2);
	for (const request of requests) {
		expect(request.selected_test_case_ids).toEqual(["TC-1"]);
		expect(request.feedback).toContain(firstNote);
	}
});

test("pasted requirements use reviewed file import and recover without changing source", async ({ page }) => {
	await fixture(page);
	let body = "";
	await page.route("**/requirements/parse", async (route) => {
		body = route.request().postData();
		await route.fulfill({ status: 503, json: { detail: "Import unavailable" } });
	});
	await page.goto(path("requirements"));
	await page.getByRole("button", { name: "Import requirements", exact: true }).click();
	await page.getByRole("radio", { name: "Paste text", exact: true }).check();
	await page.getByLabel("Requirements text").fill("Customers receive one durable receipt per order.");
	await page.getByRole("button", { name: "Parse Requirements", exact: true }).click();
	await expect.poll(() => body).toContain('filename="pasted-requirements.md"');
	expect(body).toContain("Customers receive one durable receipt per order.");
	expect(body).toContain('name="import_mode"\r\n\r\nreview');
	await page.reload();
	await page.getByRole("button", { name: "Import requirements", exact: true }).click();
	await page.getByRole("radio", { name: "Paste text", exact: true }).check();
	await expect(page.getByLabel("Requirements text")).toHaveValue("Customers receive one durable receipt per order.");
});

test("draft recovery isolates users and rejects changed project revisions", async ({ page }) => {
	const { api, project } = await fixture(page);
	await page.goto(path("use-cases"));
	const status = page.getByRole("combobox", { name: "Review status for REQ-101-SCN-01", exact: true });
	await status.selectOption("approved");
	project.current_revision++;
	api.setProject(project);
	await page.reload();
	await expect(page.getByRole("alert")).toContainText("Project changed while you were editing");
	await expect(page.getByRole("button", { name: "Save reviews", exact: true })).toBeDisabled();
	expect(api.requests.review).toHaveLength(0);
	await seedAuthenticatedSession(page, { sub: "another-reviewer" });
	await page.route("**/auth/me", (route) =>
		route.fulfill({ json: { sub: "another-reviewer", email: "reviewer@example.com", name: "Second reviewer", picture: null } })
	);
	await page.reload();
	await expect(status).toHaveValue("needs_review");
	await expect(page.getByRole("button", { name: "Save reviews", exact: true })).toHaveCount(0);
});

test("clarification queue records source, deferral and scope exclusion without expectations", { tag: "@p1" }, async ({ page }) => {
	const { api, project } = await fixture(page);
	let findings = [];
	const actions = [];
	await page.route(`**/projects/${USE_CASE_PROJECT_ID}/knowledge`, (route) =>
		route.fulfill({ json: { entries: [], skills: [], features: {} } })
	);
	await page.route(`**/projects/${USE_CASE_PROJECT_ID}/quality-findings`, async (route) => {
		if (route.request().method() === "POST") {
			const body = route.request().postDataJSON();
			actions.push(body);
			findings = [{ ...body, id: "question-1", status: "open" }];
			project.current_revision++;
			api.setProject(project);
			return route.fulfill({ json: { finding: findings[0], project_revision: project.current_revision } });
		}
		return route.fulfill({ json: { findings, project_revision: project.current_revision } });
	});
	await page.route(`**/projects/${USE_CASE_PROJECT_ID}/quality-findings/question-1`, async (route) => {
		const body = route.request().postDataJSON();
		actions.push(body);
		findings[0] = { ...findings[0], status: body.action === "defer" ? "deferred" : "excluded", disposition_reason: body.reason };
		project.current_revision++;
		api.setProject(project);
		await route.fulfill({ json: { finding: findings[0], project_revision: project.current_revision } });
	});
	await page.goto(path("context"));
	const queue = page.getByRole("region", { name: "Business clarifications" });
	await queue.getByText("Ask about a missing business rule", { exact: true }).click();
	await queue.getByLabel("Requirement needing clarification").selectOption("REQ-202");
	await queue.getByLabel("What needs clarification?").fill("What amount requires supervisor approval?");
	await queue.getByRole("button", { name: "Add clarification", exact: true }).click();
	await expect(queue).toContainText("REQ-202 · open");
	expect(actions[0].source_snapshot_id).toBe(project.current_snapshots.requirements.snapshot_id);
	expect(actions[0].expected_behavior).toBe("Unresolved: a source-backed business decision is required.");
	const panel = page.getByRole("region", { name: "Shared project corrections" });
	for (const action of ["Defer", "Exclude"]) {
		await queue.getByRole("button", { name: "Answer REQ-202", exact: true }).click();
		await panel.getByText("Defer or exclude this scope", { exact: true }).click();
		await panel.getByLabel("Reason for the scope decision").fill("Policy owner will confirm next week.");
		await panel.getByRole("button", { name: `${action} affected scope`, exact: true }).click();
		await expect(queue).toContainText(action === "Defer" ? "REQ-202 · deferred" : "REQ-202 · excluded");
	}
	expect(actions.map((a) => a.action).filter(Boolean)).toEqual(["defer", "exclude"]);
	expect(project.current_snapshots.requirements.payload.requirements[0].review_status).toBe("Approved");
	await page.screenshot({ path: test.info().outputPath("clarification-queue.png"), fullPage: true });
});

test("saved review collections start within 240px and show at least eight rows on a laptop", { tag: "@p1" }, async ({ page }) => {
	await page.setViewportSize({ width: 1366, height: 768 });
	const project = useCaseProjectFixture({ reviewState: "approved" });
	project.current_snapshots.requirements.payload.requirements = Array.from({ length: 12 }, (_, index) => ({
		id: `REQ-${101 + index}`,
		text: `Show inventory threshold and availability for item ${index + 1}.`,
		review_status: "Approved",
	}));
	for (const group of project.current_snapshots.use_cases.payload.coverage_plan) {
		group.scenarios = Array.from({ length: 6 }, (_, index) => ({
			...group.scenarios[index % 2],
			id: `${group.requirement_id}-SCN-0${index + 1}`,
		}));
	}
	await fixture(page, 12, project);
	await page.route(`**/projects/${USE_CASE_PROJECT_ID}/requirement-imports`, (route) => route.fulfill({ json: [] }));

	for (const [stage, selector] of [
		["requirements", ".requirement-row"],
		["use-cases", ".scenario-review-row"],
		["test-cases", ".test-review-item"],
	]) {
		await page.goto(path(stage));
		const rows = page.locator(selector);
		await expect(rows).toHaveCount(12);
		await expect.poll(async () => (await rows.first().boundingBox()).y, { message: stage }).toBeLessThanOrEqual(240);
		for (const row of (await rows.all()).slice(0, 8)) await expect(row).toBeInViewport({ ratio: 1 });
		await page.screenshot({ path: test.info().outputPath(`${stage}-1366.png`) });
	}
});

test("requirements search and status filter retain access to full row detail", async ({ page }) => {
	const project = useCaseProjectFixture();
	project.current_snapshots.requirements.payload.requirements[0].review_status = "Draft";
	await fixture(page, 12, project);
	await page.route(`**/projects/${USE_CASE_PROJECT_ID}/requirement-imports`, (route) => route.fulfill({ json: [] }));
	await page.goto(path("requirements"));
	const table = page.getByRole("region", { name: "Project requirements table" });
	await page.getByRole("button", { name: "Review pending requirements" }).click();
	await expect(table.locator(".requirement-row")).toHaveCount(1);
	await expect(table).toContainText("REQ-101");
	await page.getByRole("combobox", { name: "Filter requirements by review status" }).selectOption("");
	await page.getByRole("searchbox", { name: "Search requirements" }).fill("high-value");
	const detail = table.getByRole("button", { name: /^Review REQ-202:/ });
	await detail.press("Enter");
	await expect(detail).toHaveAttribute("aria-expanded", "true");
	await expect(page.getByRole("combobox", { name: "Review status for REQ-202" })).toHaveValue("Approved");
	await detail.press("Enter");
	await expect(detail).toBeFocused();
});
