import { readFile } from "node:fs/promises";
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { expectNoDocumentOverflow } from "./support/layout.js";
import { USE_CASE_PROJECT_ID, useCaseOrchestratorFixture, useCaseProjectFixture } from "./support/use-case-review.js";
import {
	createDeferred,
	installWorkspaceApi,
	workspaceProjectFixture,
	workspaceReportFixture,
	workspaceRunFixture,
	workspaceSummaryFixture,
} from "./support/workspace.js";

const v2 = process.env.E2E_PROJECT_WORKSPACES_V2 === "true";
const root = `/projects/${USE_CASE_PROJECT_ID}`;
const suiteId = "reviewed-checkout-design-v2";
const reportId = "reviewed-checkout-report-v1";
const newerReportId = "different-checkout-report-v2";
const caseId = "TC-REVIEWED-ORDER";
const runSnapshotId = "checkout-execution-snapshot-before-repair";
const priorRunFinding = "Classify the checkout failure from the recorded run.";
const unrelatedFinding = "A later run needs separate investigation.";
const literal = "  QA–01  ";

async function setup(
	page,
	{ stale = false, available = true, oldServer = false, runEvidence, browserExecution = false, supersedeExport = false } = {}
) {
	const project = useCaseProjectFixture({ name: "Verified checkout design" });
	const plan = project.current_snapshots.use_cases;
	const [firstGroup] = plan.payload.coverage_plan;
	const coverage = [{ ...firstGroup, scenarios: [firstGroup.scenarios[0]] }];
	plan.payload.coverage_plan = coverage;
	plan.approved = true;
	plan.operation = "use_cases.generate";
	project.stage_state.use_cases.approved = true;
	project.stage_state.use_cases.metadata.latest_human_review = {
		decision: "approve",
		review_id: "review-current-checkout-plan",
		snapshot_id: plan.snapshot_id,
		reviewer_user_id: project.owner_user_id,
	};
	const [, deferred] = project.current_snapshots.requirements.payload.requirements;
	deferred.clarification_status = "deferred";
	const testCase = {
		id: caseId,
		title: "Inspect the confirmed checkout order",
		description: "Check the order confirmation and identifier after checkout.",
		status: "Draft",
		automation_status: "Manual",
		linked_requirement_ids: ["REQ-101"],
		scenario_refs: [firstGroup.scenarios[0].id],
		artifact_set_id: "saved-checkout-suite",
		artifact_item_id: "confirmed-order-item",
		artifact_version_id: "confirmed-order-version-2",
		artifact_version_number: 2,
		execution_prerequisites: [
			{ name: "checkoutFixture", kind: "test_data", description: "Provision the checkout fixture.", status: "missing" },
		],
		steps: [
			{ step: 1, action: "Submit checkout", expected: "The order confirmation appears", test_data: literal },
			{ step: 2, action: "Read the confirmed order identifier", expected: "The confirmation displays the order ID", test_data: null },
		],
	};
	const sourceIds = { requirements: project.current_snapshots.requirements.snapshot_id, use_cases: plan.snapshot_id };
	const metadata = { source_snapshot_ids: sourceIds, source_use_case_snapshot_id: plan.snapshot_id };
	project.current_snapshots.test_cases = {
		snapshot_id: suiteId,
		project_id: project.project_id,
		stage: "test_cases",
		version: 2,
		project_revision: project.current_revision,
		approved: browserExecution,
		operation: "testcases.generate",
		source_snapshot_id: plan.snapshot_id,
		metadata,
		payload: {
			test_cases: [testCase],
			coverage_plan: coverage,
			approved: browserExecution,
			review: {
				approved: browserExecution,
				score: 100,
				threshold: 90,
				summary: browserExecution ? "Approved for browser execution." : "The checkout execution fixture is missing.",
			},
			substantive_assessment: {
				status: "incomplete",
				design_status: "assessed_complete",
				execution_status: "blocked",
				execution_blockers: [{ test_case_id: caseId, prerequisite_name: "checkoutFixture" }],
				obligations: [],
			},
			generation_tasks: [],
		},
	};
	project.stage_state.test_cases = { current_snapshot_id: suiteId, version: 2, stale, approved: browserExecution, metadata };
	const historicalRun = workspaceRunFixture({
		project_id: project.project_id,
		run_id: "checkout-run-before-repair",
		run_record_id: "checkout-run-record-before-repair",
		snapshot_id: runSnapshotId,
		results: [
			{ id: "prior-checkout-candidate", source_test_case_id: caseId, title: "Saved checkout browser case", status: "failed", issues: [] },
		],
		source_snapshot_id: "checkout-suite-before-repair",
		status: "failed",
		historical: true,
		selected_count: 1,
		executed_count: 1,
		passed_count: 0,
		failed_count: 1,
		summary: { passed: 0, failed: 1, skipped: 0, invalid: 0 },
	});
	project.execution_runs = [historicalRun];
	let browserCase = null;
	let matchingRun = null;
	if (browserExecution || runEvidence === "matching-history") {
		browserCase = {
			...testCase,
			id: "TC-BROWSER-RECEIPT",
			title: "Check the browser receipt",
			automation_status: "Automated",
			artifact_item_id: "browser-receipt-item",
			artifact_version_id: "browser-receipt-version-1",
			artifact_version_number: 1,
			execution_prerequisites: [],
			steps: [{ step: 1, action: "Submit checkout", expected: "The exact saved receipt appears." }],
		};
		project.current_snapshots.test_cases.payload.test_cases.push(browserCase);
	}
	if (browserExecution) {
		project.current_snapshots.test_cases.payload.test_cases.push({
			...browserCase,
			id: "TC-BROWSER-SUBMIT",
			title: "Check the checkout submit control",
			artifact_item_id: "browser-submit-item",
			artifact_version_id: "browser-submit-version-1",
			steps: [{ step: 1, action: "Open checkout", expected: "The submit control is enabled." }],
		});
	}
	if (runEvidence === "prior-suite") {
		project.current_snapshots.execution = {
			snapshot_id: runSnapshotId,
			project_id: project.project_id,
			stage: "execution",
			version: 1,
			operation: "execution.run",
			source_snapshot_id: historicalRun.source_snapshot_id,
			payload: historicalRun,
		};
		project.stage_state.execution = { current_snapshot_id: runSnapshotId, version: 1, stale: false };
	} else if (runEvidence === "matching-history") {
		project.current_revision += 2;
		const indexedRun = workspaceRunFixture({
			project_id: project.project_id,
			project_revision: project.current_revision - 1,
			run_id: "checkout-current-suite-browser-run",
			run_record_id: "checkout-current-suite-run-record",
			snapshot_id: "checkout-current-run-history-snapshot",
			source_snapshot_id: suiteId,
			status: "passed",
			historical: true,
			selected_count: 1,
			executed_count: 1,
			passed_count: 1,
			summary: { passed: 1, failed: 0, skipped: 0, invalid: 0 },
		});
		matchingRun = {
			...indexedRun,
			results: [
				{ id: "checkout-receipt-candidate", source_test_case_id: browserCase.id, title: browserCase.title, status: "passed", issues: [] },
			],
		};
		project.execution_runs.unshift(indexedRun);
	}
	if (!oldServer) {
		project.design_delivery_enabled = true;
		project.reviewed_design_available = available && !stale;
		project.suite_quality = {
			suite_snapshot_id: suiteId,
			source_freshness: stale ? "stale" : available ? "current" : "unknown",
			design_status: available && !stale ? "complete" : "unknown",
			scenario_decision: "approved",
			scenario_snapshot_id: plan.snapshot_id,
			scenario_review_id: "review-current-checkout-plan",
			human_acceptance: "unavailable",
			declared_setup: "blocked",
			required_requirement_ids: ["REQ-101"],
			deferred_requirement_ids: ["REQ-202"],
			excluded_requirement_ids: [],
			active_case_count: project.current_snapshots.test_cases.payload.test_cases.length,
			deprecated_case_count: 0,
			reason_codes: [stale ? "sources_stale" : available ? "design_verified" : "design_proof_missing"],
		};
	}

	const workspaceProject = workspaceProjectFixture({
		project_id: project.project_id,
		project_name: project.name,
		name: project.name,
		project_revision: project.current_revision,
		current_stage: "test_cases",
		current_snapshot_id: suiteId,
	});
	const summary = workspaceSummaryFixture({ projects: [workspaceProject] });
	const writes = [];
	const errors = [];
	page.on("pageerror", (error) => errors.push(error.message));
	page.on("request", (request) => {
		if (["fetch", "xhr"].includes(request.resourceType()) && !["GET", "HEAD", "OPTIONS"].includes(request.method())) {
			writes.push(`${request.method()} ${new URL(request.url()).pathname}`);
		}
	});
	await page.route("**/*", (route) => {
		const request = route.request();
		if (!["fetch", "xhr"].includes(request.resourceType())) return route.fallback();
		const pathname = new URL(request.url()).pathname;
		if (pathname.endsWith("/quality-findings")) {
			const finding = {
				status: "open",
				category: "unknown",
				expected_behavior: "Preserve the expected checkout confirmation.",
				prerequisites: [],
				environment: "staging",
				source_stage: "execution",
				source_test_case_snapshot_id: historicalRun.source_snapshot_id,
				test_case_ids: [caseId],
				requirement_ids: ["REQ-101"],
			};
			return route.fulfill({
				json: {
					findings: [
						{ ...finding, id: "prior-run-finding", summary: priorRunFinding, source_snapshot_id: runSnapshotId },
						{ ...finding, id: "different-run-finding", summary: unrelatedFinding, source_snapshot_id: "different-execution-snapshot" },
					],
					project_revision: project.current_revision,
				},
			});
		}
		if (pathname.endsWith("/knowledge")) return route.fulfill({ json: { entries: [], skills: [], features: {}, revision: "0" } });
		return route.fulfill({ status: 404, json: { detail: "No fixture for this optional read." } });
	});
	const orchestrator = { ...useCaseOrchestratorFixture(project), reviewed_design_available: project.reviewed_design_available };
	await installWorkspaceApi(page, {
		summary,
		projectDetails: { [project.project_id]: project },
		orchestratorStatuses: { [project.project_id]: orchestrator },
	});
	const executionReads = [];
	for (const run of [historicalRun, matchingRun].filter(Boolean)) {
		await page.route(`**${root}/execution-runs/${run.run_record_id}`, (route) => {
			expect(route.request().method()).toBe("GET");
			executionReads.push(run.run_record_id);
			return route.fulfill({ json: run });
		});
	}
	const exportRequests = [];
	const reportReads = [];
	let publishedReport;
	await page.route(`**${root}/reports/*`, (route) => {
		const id = new URL(route.request().url()).pathname.split("/").at(-1);
		expect(route.request().method()).toBe("GET");
		reportReads.push(id);
		if (id !== publishedReport?.snapshot_id) return route.fulfill({ status: 404 });
		return route.fulfill({
			json: {
				project_id: project.project_id,
				report_id: reportId,
				project_revision: publishedReport.project_revision,
				format: publishedReport.payload.format,
				created_at: "2026-07-17T12:35:00Z",
				delivery_kind: "reviewed_design",
				legacy_approved: false,
				historical: true,
				stale: false,
				evidence: {
					suite_snapshot_id: suiteId,
					source_snapshot_ids: { ...sourceIds, test_cases: suiteId },
					exported_at: "2026-07-17T12:35:00Z",
					case_versions: [{ case_id: caseId, case_version_id: testCase.artifact_version_id, case_version_number: 2 }],
				},
			},
		});
	});
	await page.route("**/export/json", (route) => {
		const request = route.request();
		exportRequests.push({ body: request.postDataJSON(), requestId: request.headers()["x-request-id"] });
		project.current_revision += 1;
		const report = {
			snapshot_id: reportId,
			project_id: project.project_id,
			stage: "reports",
			version: 1,
			project_revision: project.current_revision,
			operation: "export.json",
			approved: false,
			source_snapshot_id: suiteId,
			metadata: { delivery_kind: "reviewed_design" },
			payload: { format: "json", source: "export", delivery_kind: "reviewed_design", approved: false },
		};
		publishedReport = report;
		project.current_snapshots.reports = report;
		project.stage_state.reports = { current_snapshot_id: reportId, version: 1, stale: false, approved: false, metadata: report.metadata };
		summary.recent_reports = [
			workspaceReportFixture({
				report_id: reportId,
				project_id: project.project_id,
				project_name: project.name,
				project_revision: project.current_revision,
				status: "draft",
				approved: false,
				count: 1,
				source_snapshot_id: suiteId,
				delivery_kind: "reviewed_design",
			}),
		];
		if (supersedeExport) {
			project.current_revision += 1;
			project.current_snapshots.reports = {
				...report,
				snapshot_id: newerReportId,
				operation: "export.csv",
				version: 2,
				project_revision: project.current_revision,
				payload: { ...report.payload, format: "csv" },
			};
			project.stage_state.reports.current_snapshot_id = newerReportId;
			project.stage_state.reports.version = 2;
		}
		orchestrator.project_revision = project.current_revision;
		return route.fulfill({
			headers: supersedeExport ? { "X-Export-Report-ID": reportId, "Access-Control-Expose-Headers": "X-Export-Report-ID" } : {},
			json: {
				test_cases: [testCase],
				audit_metadata: {
					snapshot_id: suiteId,
					is_draft: false,
					design_delivery: { delivery_kind: "reviewed_design", declared_setup: "blocked" },
				},
			},
		});
	});
	await seedAuthenticatedSession(page);
	return { errors, exportRequests, writes, testCase, historicalRun, matchingRun, browserCase, executionReads, reportReads, project };
}

const quality = (page) => page.getByRole("region", { name: "Saved suite quality", exact: true });
const exportDesign = (page) => page.getByRole("button", { name: "Export reviewed design as JSON API/Import ready", exact: true });

test.describe("Exact suite evidence across Review and Deliver", () => {
	test.skip(!v2, "Requires the opt-in project workspaces");

	test("delivers the exact reviewed design while browser setup stays blocked", async ({ page }, testInfo) => {
		const { errors, exportRequests, writes, testCase, historicalRun } = await setup(page);
		await page.setViewportSize({ width: 1440, height: 900 });
		await page.goto(`${root}/review/tests`);
		await expect(quality(page)).toContainText(/Independent design\s*Complete/);
		await expect(quality(page)).toContainText(/Source freshness\s*Current/);
		await expect(quality(page)).toContainText(/Scenario approval\s*Approved/);
		await expect(quality(page)).toContainText(/Human suite acceptance\s*Unavailable/);
		await expect(quality(page)).toContainText(/Declared setup\s*Blocked/);
		await expect(quality(page)).toContainText("No matching run evidence");
		await page.getByRole("tab", { name: /Generated Test Cases/ }).press("Enter");
		const search = page.getByRole("searchbox", { name: "Search test cases", exact: true });
		await search.fill("confirmed order identifier");
		const savedCase = page.getByRole("button", { name: `${testCase.id} ${testCase.title}`, exact: true });
		await savedCase.press("Enter");
		const reader = page.getByRole("region", { name: "Selected test case", exact: true });
		await expect(reader).toContainText(testCase.steps[1].expected);
		await expectNoDocumentOverflow(page, "Review suite evidence");
		await page.screenshot({ path: testInfo.outputPath("reviewed-design-review.png") });
		expect(writes).toEqual([]);

		const continueToExport = page.getByRole("button", { name: "Continue to export", exact: true });
		await continueToExport.press("Enter");
		await expect(page).toHaveURL(`${root}/deliver`);
		await expect(page.getByRole("heading", { name: "Reviewed design ready to export", exact: true })).toBeVisible();
		const formatButtons = page.locator(".export-buttons");
		const formats = formatButtons.getByRole("button");
		await expect(formats).toHaveCount(3);
		for (const format of await formats.all()) await expect(format).toBeInViewport({ ratio: 0.75 });
		await page.screenshot({ path: testInfo.outputPath("reviewed-design-initial-export.png") });
		await formatButtons.scrollIntoViewIfNeeded();
		const accessibility = await new AxeBuilder({ page })
			.include(".export-buttons")
			.withRules(["color-contrast", "button-name", "label-content-name-mismatch"])
			.analyze();
		expect(accessibility.violations).toEqual([]);
		const excelFormat = formatButtons.getByRole("button", { name: "Export reviewed design as Excel Formatted .xlsx", exact: true });
		await expect(formatButtons.getByRole("button", { name: "Export reviewed design as CSV Excel compatible", exact: true })).toBeEnabled();
		await expect(excelFormat).toBeEnabled();
		await expect(exportDesign(page)).toBeEnabled();
		await expect(page.getByText("Export locked by review gate", { exact: true })).toHaveCount(0);
		await expect(page.getByRole("checkbox", { name: "Export draft anyway", exact: true })).toHaveCount(0);
		await expect(page.getByText("No Playwright execution report matches this saved suite.", { exact: true })).toBeVisible();
		const reportHeading = page.getByRole("heading", { name: "Playwright Execution Report", exact: true });
		expect(
			await reportHeading.evaluate((heading) =>
				Boolean(
					heading.compareDocumentPosition(heading.closest("main").querySelector(".export-buttons")) & Node.DOCUMENT_POSITION_PRECEDING
				)
			)
		).toBe(true);
		await page.screenshot({ path: testInfo.outputPath("reviewed-design-deliver.png") });

		await page.getByRole("button", { name: "Back to tests", exact: true }).press("Enter");
		await expect(page).toHaveURL(`${root}/review/tests`);
		await expect(search).toHaveValue("confirmed order identifier");
		await expect(savedCase).toHaveAttribute("aria-pressed", "true");
		await expect(reader).toContainText(testCase.steps[1].expected);
		await continueToExport.press("Enter");
		await expect(page).toHaveURL(`${root}/deliver`);

		const sections = page.getByRole("navigation", { name: "Workspace sections", exact: true });
		await sections.getByRole("link", { name: "Browser tests", exact: true }).press("Enter");
		await expect(page).toHaveURL(`${root}/deliver/browser-tests`);
		await expect(page.getByRole("button", { name: "Run 0 Candidates", exact: true })).toBeDisabled();
		await expect(page.getByText("No preview yet. Preview execution readiness for the current test cases.", { exact: true })).toBeVisible();
		await sections.getByRole("link", { name: "Export", exact: true }).press("Enter");
		await expect(page).toHaveURL(`${root}/deliver`);
		await expect(exportDesign(page)).toBeEnabled();
		expect(writes).toEqual([]);
		expect(exportRequests).toEqual([]);

		await excelFormat.focus();
		await page.keyboard.press("Tab");
		await expect(exportDesign(page)).toBeFocused();
		const downloadPromise = page.waitForEvent("download");
		await page.keyboard.press("Enter");
		const download = await downloadPromise;
		const file = JSON.parse(await readFile(await download.path(), "utf8"));
		expect(file.audit_metadata).toMatchObject({ snapshot_id: suiteId, is_draft: false });
		expect(file.test_cases[0].steps).toEqual(testCase.steps);
		expect(exportRequests).toHaveLength(1);
		const [{ body, requestId }] = exportRequests;
		expect(requestId).toBeTruthy();
		expect(body).toMatchObject({
			project_id: USE_CASE_PROJECT_ID,
			source_snapshot_id: suiteId,
			delivery_intent: "reviewed_design",
			test_cases: [testCase],
		});
		expect(body).not.toHaveProperty("approved");
		expect(body).not.toHaveProperty("review");
		expect(body).not.toHaveProperty("draft_override_requested");
		expect(body).not.toHaveProperty("draft_override_reason");
		await expect(page.getByText("Reviewed design exported as JSON", { exact: true }).first()).toBeVisible();
		await expect(page.getByRole("link", { name: "Open saved report for this download", exact: true })).toHaveCount(0);
		await expect(page.getByRole("link", { name: "Open latest saved file report", exact: true })).toHaveAttribute(
			"href",
			`${root}/deliver?report=${reportId}`
		);

		const savedRunPath = `${root}/reports?run=${historicalRun.run_record_id}`;
		await page.goto(savedRunPath);
		await expect(page).toHaveURL(savedRunPath);
		await expect(page.getByRole("heading", { name: "Saved execution", exact: true })).toBeVisible();
		await expect(page.getByText(`Run ${historicalRun.run_id}`, { exact: true })).toBeVisible();
		await expect(page.getByText(historicalRun.source_snapshot_id, { exact: true })).toBeVisible();
		await expect(exportDesign(page)).toHaveCount(0);

		await page.goto("/reports");
		const row = page.getByRole("listitem").filter({ hasText: reportId });
		await expect(row.locator('[data-status-kind="report"][data-status-value="reviewed_design"]')).toContainText("Reviewed design");
		await expect(row.getByText(reportId, { exact: true })).toBeVisible();
		await expect(row.getByRole("link", { name: /Open report evidence for Verified checkout design/i })).toHaveAttribute(
			"href",
			new RegExp(`/projects/project-use-case-review/(reports|deliver)\\?report=${reportId}$`)
		);
		expect(writes).toEqual(["POST /export/json"]);
		expect(errors).toEqual([]);
	});

	test("links a completed download to its own saved file after a newer publication", async ({ page }, testInfo) => {
		const { writes, reportReads, errors, testCase, exportRequests } = await setup(page, { supersedeExport: true });
		const releaseProjectRead = createDeferred();
		let waitingForProjectRead = false;
		await page.route(`**${root}`, async (route) => {
			if (exportRequests.length) {
				waitingForProjectRead = true;
				await releaseProjectRead.promise;
			}
			await route.fallback();
		});
		await page.setViewportSize({ width: 1366, height: 900 });
		await page.goto(`${root}/deliver`);
		const downloadEvent = page.waitForEvent("download");
		await exportDesign(page).press("Enter");
		const download = await downloadEvent;
		const file = JSON.parse(await readFile(await download.path(), "utf8"));
		expect(file.test_cases[0].steps).toEqual(testCase.steps);
		await expect.poll(() => waitingForProjectRead).toBe(true);
		const downloadedReport = page.getByRole("link", { name: "Open saved report for this download", exact: true });
		await expect(downloadedReport).toHaveAttribute("href", `${root}/deliver?report=${reportId}`, { timeout: 4000 });
		releaseProjectRead.resolve();
		await expect(page.getByRole("link", { name: "Open latest saved file report", exact: true })).toHaveAttribute(
			"href",
			`${root}/deliver?report=${newerReportId}`
		);
		await expect(downloadedReport).toBeInViewport();
		await expect(page).toHaveURL(`${root}/deliver`);
		expect(reportReads).toEqual([]);
		await expectNoDocumentOverflow(page);
		await page.screenshot({ path: testInfo.outputPath("exact-download-report-link.png") });
		await downloadedReport.press("Enter");
		const report = page.getByRole("region", { name: "Saved file report", exact: true });
		await expect(report).toContainText(reportId);
		await expect(report).toContainText("Historical");
		await expect(report).toContainText("Reviewed design");
		await expect(report).toContainText("JSON");
		await report.getByText("Original saved cases", { exact: true }).press("Enter");
		await expect(report).toContainText(testCase.artifact_version_id);
		await page.screenshot({ path: testInfo.outputPath("exact-download-saved-report.png") });
		expect(reportReads).toEqual([reportId]);
		expect(writes).toEqual(["POST /export/json"]);
		expect(errors).toEqual([]);
	});

	test("opens declared setup and returns from exact failed-run findings without dispatch", async ({ page }, testInfo) => {
		const { errors, exportRequests, writes, historicalRun, executionReads } = await setup(page);
		await page.setViewportSize({ width: 1440, height: 900 });
		const prerequisites = (page) => page.getByRole("region", { name: "Declared browser prerequisites", exact: true });
		await page.goto(`${root}/deliver`);
		await expect(exportDesign(page)).toBeEnabled();
		await expect(prerequisites(page)).toContainText("checkoutFixture");
		await page.goto(`${root}/deliver/browser-tests`);
		await expect(page.getByRole("button", { name: "Run 0 Candidates", exact: true })).toBeDisabled();
		const setupLink = prerequisites(page).getByRole("link", { name: `Review prerequisite checkoutFixture for ${caseId}`, exact: true });
		await setupLink.press("Enter");
		await expect(page).toHaveURL(
			new RegExp(`[?&]snapshot=${suiteId}&case=${caseId}&prerequisite=checkoutFixture&setup=declared#quality-feedback-heading$`)
		);
		await expect(page.getByRole("heading", { name: "Quality feedback", exact: true })).toBeFocused();
		await expect(page.getByLabel("Missing prerequisite name")).toHaveValue("checkoutFixture");
		expect(writes).toEqual([]);

		const savedRunPath = `${root}/deliver?run=${historicalRun.run_record_id}`;
		await page.goto(savedRunPath);
		await expect(prerequisites(page)).toHaveCount(0);
		const findingLink = page.getByRole("link", { name: "Review quality findings for this run", exact: true });
		await expect(findingLink).toHaveAttribute(
			"href",
			new RegExp(`[?&]finding_source=${runSnapshotId}&finding_run=${historicalRun.run_record_id}#quality-feedback-heading$`)
		);
		await findingLink.press("Enter");
		await expect(page.getByRole("heading", { name: "Quality feedback", exact: true })).toBeFocused();
		const runFindings = page.getByRole("list", { name: "Quality findings for this run", exact: true });
		await expect(runFindings.getByText(priorRunFinding, { exact: true })).toBeVisible();
		await expect(page.getByText(unrelatedFinding, { exact: true })).toHaveCount(0);
		await runFindings.getByRole("button", { name: "Review finding", exact: true }).click();
		await expect(page.getByText(priorRunFinding, { exact: false }).last()).toBeVisible();
		const backToRun = page.getByRole("link", { name: "Back to saved run", exact: true });
		await expect(backToRun).toHaveAttribute("href", savedRunPath, { timeout: 5000 });
		await backToRun.focus();
		await page.screenshot({ path: testInfo.outputPath("verified-run-finding-return.png") });
		await page.keyboard.press("Enter");
		await expect(page).toHaveURL(savedRunPath);
		const savedRun = page.getByRole("main", { name: "Workflow workspace: Deliver · Saved execution", exact: true });
		await expect(savedRun.getByRole("heading", { level: 1, name: "Deliver · Saved execution", exact: true })).toBeVisible();
		await expect(savedRun.getByRole("region", { name: "Contextual task", exact: true })).toHaveCount(0);
		await expect(savedRun.getByText("Reviewed design available", { exact: true })).toHaveCount(0);
		await expect(page.getByRole("heading", { name: "Saved execution", exact: true })).toBeVisible();
		await expect(findingLink).toBeVisible();
		await expect(page.getByText(`Run ${historicalRun.run_id}`, { exact: true })).toBeVisible();
		await expect(page.getByText(historicalRun.source_snapshot_id, { exact: true })).toBeVisible();
		await expect(page.getByText("Case outcomes (1)", { exact: true })).toBeVisible();
		expect(executionReads).toEqual(Array(3).fill(historicalRun.run_record_id));
		await page.screenshot({ path: testInfo.outputPath("returned-exact-saved-run.png") });
		await page.goBack();
		await expect(runFindings.getByText(priorRunFinding, { exact: true })).toBeVisible();
		await page.goto(
			`${root}/inputs/context?finding_source=different-execution-snapshot&finding_run=${historicalRun.run_record_id}#quality-feedback-heading`
		);
		await expect(page.getByText("This saved run's quality findings are unavailable.", { exact: true })).toBeVisible();
		await expect(page.getByText(unrelatedFinding, { exact: true })).toHaveCount(0);
		await expect(page.getByRole("button", { name: "Review finding", exact: true })).toHaveCount(0);
		await expect(backToRun).toHaveCount(0);
		expect(writes).toEqual([]);
		expect(exportRequests).toEqual([]);
		expect(errors).toEqual([]);
	});

	test("keeps a previous suite's failed run separate from current Browser tests", async ({ page }, testInfo) => {
		const { writes, errors, historicalRun, executionReads } = await setup(page, { runEvidence: "prior-suite" });
		await page.setViewportSize({ width: 1440, height: 900 });
		await page.goto(`${root}/review/tests`);
		await expect(quality(page)).toContainText(/Execution result\s*No matching run evidence/);
		await page
			.getByRole("navigation", { name: "Project workspaces", exact: true })
			.getByRole("link", { name: "Deliver", exact: true })
			.press("Enter");
		await expect(exportDesign(page)).toBeEnabled();
		const noReport = page.getByText("No Playwright execution report matches this saved suite.", { exact: true });
		await expect(noReport).toBeVisible();
		await page
			.getByRole("navigation", { name: "Workspace sections", exact: true })
			.getByRole("link", { name: "Browser tests", exact: true })
			.press("Enter");
		await expect(page).toHaveURL(`${root}/deliver/browser-tests`);
		await expect(noReport).toBeVisible();
		await expect(page.getByText(/Execution failed|checkout-run-before-repair/)).toHaveCount(0);
		await expect(page.getByRole("region", { name: "Execution results table", exact: true })).toHaveCount(0);
		await expect(page.getByRole("region", { name: "Declared browser prerequisites", exact: true })).toContainText("checkoutFixture");
		await expect(page.getByRole("button", { name: "Run 0 Candidates", exact: true })).toBeDisabled();
		expect(executionReads).toEqual([]);
		const previous = page.getByRole("link", { name: "Open saved run for a previous suite", exact: true });
		const savedRunPath = `${root}/deliver?run=${historicalRun.run_record_id}`;
		await expect(previous).toHaveAttribute("href", savedRunPath);
		await page.screenshot({ path: testInfo.outputPath("browser-previous-run-separated.png"), fullPage: true });
		await previous.press("Enter");
		await expect(page).toHaveURL(savedRunPath);
		await expect(page.getByRole("heading", { name: "Saved execution", exact: true })).toBeVisible();
		await expect(page.getByText(`Run ${historicalRun.run_id}`, { exact: true })).toBeVisible();
		await expect(page.getByText(historicalRun.source_snapshot_id, { exact: true })).toBeVisible();
		await expect(page.getByText(/Historical run from project revision/)).toBeVisible();
		expect(executionReads).toEqual([historicalRun.run_record_id]);
		expect(writes).toEqual([]);
		expect(errors).toEqual([]);
	});

	test("reads an exact matching saved run and returns from its case to Deliver evidence", async ({ page }, testInfo) => {
		const { writes, errors, historicalRun, matchingRun, browserCase, executionReads } = await setup(page, {
			runEvidence: "matching-history",
		});
		const caseReads = [];
		await page.route(`**${root}/test-case-evidence?*`, (route) => {
			const request = route.request();
			expect(request.method()).toBe("GET");
			caseReads.push(Object.fromEntries(new URL(request.url()).searchParams));
			return route.fulfill({
				json: {
					project_id: USE_CASE_PROJECT_ID,
					snapshot_id: suiteId,
					suite_version: 2,
					project_revision: 7,
					historical: false,
					test_case: browserCase,
				},
			});
		});
		await page.setViewportSize({ width: 1440, height: 900 });
		await page.goto(`${root}/review/tests`);
		await expect(quality(page)).toContainText(/Execution result\s*Passed/);
		await page
			.getByRole("navigation", { name: "Project workspaces", exact: true })
			.getByRole("link", { name: "Deliver", exact: true })
			.press("Enter");
		await expect(quality(page)).toContainText(/Execution result\s*Passed/);
		await expect(page.getByText(`Run ${matchingRun.run_id}`, { exact: true })).toBeVisible();
		const browserPath = `${root}/deliver/browser-tests?report=unrelated-file#unrelated`;
		await page.goto(browserPath);
		await expect(page).toHaveURL(browserPath);
		await expect(page.getByRole("region", { name: "Automation evidence", exact: true })).toContainText("Run recorded for this suite");
		const report = page.locator(".playwright-report-card");
		await expect(report.getByText(`Run ${matchingRun.run_id}`, { exact: true })).toBeVisible();
		await expect(report.getByText(suiteId, { exact: true })).toBeVisible();
		await expect(page.getByRole("link", { name: "Open saved run for a previous suite", exact: true })).toHaveAttribute(
			"href",
			`${root}/deliver?run=${historicalRun.run_record_id}`
		);
		await expect(page.getByRole("button", { name: "Run 0 Candidates", exact: true })).toBeDisabled();
		await report.getByText("Case outcomes (1)", { exact: true }).press("Enter");
		const link = report.getByRole("link", { name: `Open saved case ${browserCase.id} ${browserCase.title}`, exact: true });
		const savedCasePath = `${root}/test-cases?suite=${suiteId}&case=${browserCase.id}`;
		await expect(link).toHaveAttribute("href", savedCasePath);
		await page.screenshot({ path: testInfo.outputPath("browser-matching-run.png"), fullPage: true });
		await link.press("Enter");
		await expect(page).toHaveURL(savedCasePath);
		const reader = page.getByRole("region", { name: "Saved test case", exact: true });
		await expect(reader).toContainText(browserCase.steps[0].expected);
		expect(caseReads).toEqual([{ snapshot_id: suiteId, case_id: browserCase.id }]);
		await page.goBack();
		await expect(page).toHaveURL(`${root}/deliver?run=${matchingRun.run_record_id}`);
		await expect(page.getByRole("heading", { name: "Saved execution", exact: true })).toBeVisible();
		await expect(link).toBeFocused();
		await expect(report).toContainText(`Run ${matchingRun.run_id}`);
		expect(executionReads).toEqual(Array(3).fill(matchingRun.run_record_id));
		expect(writes).toEqual([]);
		expect(errors).toEqual([]);
	});

	test("requires a deliberate browser candidate selection for each fresh preview", async ({ page }, testInfo) => {
		const { errors, writes, project } = await setup(page, { browserExecution: true });
		const savedCases = project.current_snapshots.test_cases.payload.test_cases;
		const [manualCase, ...automatedCases] = savedCases;
		const [receipt, submit] = automatedCases.map((savedCase) => ({
			id: `candidate-${savedCase.id}`,
			source_test_case_id: savedCase.id,
			title: savedCase.title,
			status: "executable",
			spec: { steps: savedCase.steps.map(({ action, expected }) => `${action}. ${expected}`) },
			traceability_ids: savedCase.linked_requirement_ids,
		}));
		const previewResponse = () => ({
			readiness: {
				run_allowed: true,
				blockers: [],
				project_id: project.project_id,
				project_revision: project.current_revision,
				source_snapshot_id: suiteId,
				target_base_url: "https://checkout.example.test/app",
				target_source: "configured_default",
			},
			executable: [receipt, submit],
			manual: [{ id: "candidate-manual", source_test_case_id: manualCase.id, title: manualCase.title, status: "manual", spec: null }],
			unsupported: [],
			invalid: [],
			warnings: [],
			summary: { executable: 2, manual: 1, unsupported: 0, invalid: 0 },
		});
		const checkExecutionRequest = (route) => {
			const request = route.request();
			expect(request.method()).toBe("POST");
			expect(request.headers()["x-request-id"]).toBeTruthy();
			const body = request.postDataJSON();
			expect(body).toMatchObject({ project_id: project.project_id, base_project_revision: project.current_revision });
			expect(body.test_cases.map((savedCase) => savedCase.id)).toEqual(savedCases.map((savedCase) => savedCase.id));
			return body;
		};
		await page.route("**/automation/execution/preview", (route) => {
			checkExecutionRequest(route);
			project.current_revision += 1;
			return route.fulfill({ json: previewResponse() });
		});
		const selections = [];
		await page.route("**/automation/execution/run", (route) => {
			const body = checkExecutionRequest(route);
			selections.push(body.selected_test_case_ids);
			project.current_revision += 1;
			return route.fulfill({
				json: {
					run_id: "mock-selected-checkout-browser-run",
					status: "passed",
					preview: previewResponse(),
					summary: { passed: 1, failed: 0, invalid: 0, skipped: 0 },
					results: [{ id: "submit-result", source_test_case_id: submit.source_test_case_id, title: submit.title, status: "passed" }],
				},
			});
		});

		await page.setViewportSize({ width: 1440, height: 900 });
		await page.goto(`${root}/deliver/browser-tests`);
		const preview = page.getByRole("button", { name: "Preview Execution", exact: true });
		const runNone = page.getByRole("button", { name: "Run 0 Candidates", exact: true });
		const executable = page.getByRole("region", { name: "Executable candidates", exact: true });
		const runOne = executable.getByRole("button", { name: "Run 1 Candidate", exact: true });
		const selectReceipt = page.getByRole("checkbox", { name: `Select ${receipt.source_test_case_id} for execution`, exact: true });
		const selectSubmit = page.getByRole("checkbox", { name: `Select ${submit.source_test_case_id} for execution`, exact: true });
		const selectAll = page.getByRole("checkbox", { name: "Select all executable candidates", exact: true });
		const expectUnselectedPreview = async () => {
			await expect(preview).toBeEnabled();
			await expect(selectReceipt).toBeEnabled();
			await expect(selectSubmit).toBeEnabled();
			await expect(selectReceipt).not.toBeChecked();
			await expect(selectSubmit).not.toBeChecked();
			await expect(selectAll).not.toBeChecked();
			await expect(executable.getByRole("button", { name: "Run 0 Candidates", exact: true })).toBeDisabled();
			await expect(page.getByText("Select eligible candidates in Execution Preview to enable Run.", { exact: true })).toBeVisible();
		};
		await expect(runNone).toBeDisabled();
		await preview.press("Enter");
		await expectUnselectedPreview();
		await expect(page.getByText(`${manualCase.id} - ${manualCase.title}`, { exact: true })).toBeVisible();
		await expect(page.getByRole("checkbox", { name: `Select ${manualCase.id} for execution`, exact: true })).toHaveCount(0);
		await page.screenshot({ path: testInfo.outputPath("browser-preview-no-selection.png"), fullPage: true });
		const previewPost = "POST /automation/execution/preview";
		expect(writes).toEqual([previewPost]);

		await selectSubmit.press("Space");
		await expect(selectSubmit).toBeChecked();
		await expect(selectReceipt).not.toBeChecked();
		await expect(runOne).toBeEnabled();
		const sections = page.getByRole("navigation", { name: "Workspace sections", exact: true });
		await sections.getByRole("link", { name: "Export", exact: true }).press("Enter");
		await expect(page).toHaveURL(`${root}/deliver`);
		await sections.getByRole("link", { name: "Browser tests", exact: true }).press("Enter");
		await expect(page).toHaveURL(`${root}/deliver/browser-tests`);
		await expect(selectSubmit).toBeChecked();
		await expect(selectReceipt).not.toBeChecked();
		await expect(runOne).toBeEnabled();
		await expect(page.getByRole("button", { name: /^Run \d+ Candidates?$/ })).toHaveCount(1);
		await selectSubmit.focus();
		await page.keyboard.press("Tab");
		await expect(runOne).toBeFocused();
		await expect(runOne).toBeInViewport({ ratio: 1 });
		await page.screenshot({ path: testInfo.outputPath("browser-run-after-candidates.png") });
		expect(writes).toEqual([previewPost]);
		expect(selections).toEqual([]);

		await page.keyboard.press("Enter");
		await expect(page.getByText("Execution passed: 1 passed, 0 failed, 0 invalid.", { exact: true })).toBeVisible();
		await expect(selectSubmit).toBeChecked();
		expect(selections).toEqual([[submit.id]]);
		await expect(preview).toBeEnabled();
		await preview.press("Enter");
		await expectUnselectedPreview();
		expect(selections).toEqual([[submit.id]]);
		expect(writes).toEqual([previewPost, "POST /automation/execution/run", previewPost]);
		expect(errors).toEqual([]);
	});

	for (const [name, options] of [
		["unknown", { available: false }],
		["stale", { stale: true }],
		["older server", { oldServer: true }],
	]) {
		test(`keeps reviewed design unavailable with ${name} evidence`, async ({ page }) => {
			const { errors, writes } = await setup(page, options);
			await page.goto(`${root}/review/tests`);
			if (!options.oldServer) await expect(quality(page)).toContainText(/Independent design\s*Unknown/);
			await page
				.getByRole("navigation", { name: "Project workspaces" })
				.getByRole("link", { name: /^Deliver/ })
				.click();
			await expect(exportDesign(page)).toHaveCount(0);
			await expect(page.getByRole("button", { name: /JSON.*API\/Import ready/ })).toBeDisabled();
			await expect(page.getByRole("region", { name: "Declared browser prerequisites", exact: true })).toHaveCount(0);
			expect(writes).toEqual([]);
			expect(errors).toEqual([]);
		});
	}
});

test("flag-off keeps the existing draft export gate despite server evidence", { tag: "@p1" }, async ({ page }) => {
	test.skip(v2, "Requires the legacy workspace build");
	const { errors, writes, historicalRun } = await setup(page);
	await page.goto(`${root}/reports`);
	await expect(page.getByText("Export locked by review gate", { exact: true })).toBeVisible();
	await expect(exportDesign(page)).toHaveCount(0);
	await expect(page.getByRole("checkbox", { name: "Export draft anyway", exact: true })).toBeVisible();
	await expect(page.getByRole("region", { name: "Declared browser prerequisites", exact: true })).toHaveCount(0);
	await page.goto(`${root}/reports?run=${historicalRun.run_record_id}`);
	await expect(page.getByText(`Run ${historicalRun.run_id}`, { exact: true })).toBeVisible();
	await expect(page.getByRole("link", { name: "Review quality findings for this run", exact: true })).toHaveCount(0);
	await page.goto(
		`${root}/inputs/context?finding_source=${runSnapshotId}&finding_run=${historicalRun.run_record_id}#quality-feedback-heading`
	);
	const findings = page.getByRole("list", { name: "Quality findings for this run", exact: true });
	await expect(findings.getByText(priorRunFinding, { exact: true })).toBeVisible();
	await expect(page.getByText(unrelatedFinding, { exact: true })).toHaveCount(0);
	await page.getByRole("link", { name: "Back to saved run", exact: true }).press("Enter");
	await expect(page).toHaveURL(`${root}/deliver?run=${historicalRun.run_record_id}`);
	await expect(page.getByText(`Run ${historicalRun.run_id}`, { exact: true })).toBeVisible();
	expect(writes).toEqual([]);
	expect(errors).toEqual([]);
});
