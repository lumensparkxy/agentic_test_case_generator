import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { expectNoDocumentOverflow } from "./support/layout.js";
import { USE_CASE_PROJECT_ID, useCaseOrchestratorFixture, useCaseProjectFixture } from "./support/use-case-review.js";
import {
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
const caseId = "TC-REVIEWED-ORDER";
const runSnapshotId = "checkout-execution-snapshot-before-repair";
const priorRunFinding = "Classify the checkout failure from the recorded run.";
const unrelatedFinding = "A later run needs separate investigation.";
const literal = "  QA–01  ";

async function setup(page, { stale = false, available = true, oldServer = false } = {}) {
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
		approved: false,
		operation: "testcases.generate",
		source_snapshot_id: plan.snapshot_id,
		metadata,
		payload: {
			test_cases: [testCase],
			coverage_plan: coverage,
			approved: false,
			review: { approved: false, score: 100, threshold: 90, summary: "The checkout execution fixture is missing." },
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
	project.stage_state.test_cases = { current_snapshot_id: suiteId, version: 2, stale, approved: false, metadata };
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
	});
	project.execution_runs = [historicalRun];
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
			active_case_count: 1,
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
	await installWorkspaceApi(page, {
		summary,
		projectDetails: { [project.project_id]: project },
		orchestratorStatuses: {
			[project.project_id]: { ...useCaseOrchestratorFixture(project), reviewed_design_available: project.reviewed_design_available },
		},
	});
	await page.route(`**/projects/${project.project_id}/execution-runs/${historicalRun.run_record_id}`, (route) =>
		route.fulfill({ json: historicalRun })
	);
	const exportRequests = [];
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
		return route.fulfill({
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
	return { errors, exportRequests, writes, testCase, historicalRun };
}

const quality = (page) => page.getByRole("region", { name: "Saved suite quality", exact: true });
const exportDesign = (page) => page.getByRole("button", { name: "Export reviewed design as JSON", exact: true });

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
		await expectNoDocumentOverflow(page, "Review suite evidence");
		await page.screenshot({ path: testInfo.outputPath("reviewed-design-review.png") });
		expect(writes).toEqual([]);

		await page
			.getByRole("navigation", { name: "Project workspaces" })
			.getByRole("link", { name: /^Deliver/ })
			.click();
		await expect(page.getByRole("heading", { name: "Reviewed design ready to export", exact: true })).toBeVisible();
		await expect(exportDesign(page)).toBeEnabled();
		await expect(page.getByText("Export locked by review gate", { exact: true })).toHaveCount(0);
		await expect(page.getByRole("checkbox", { name: "Export draft anyway", exact: true })).toHaveCount(0);
		await expect(page.getByText("No Playwright execution report matches this saved suite.", { exact: true })).toBeVisible();
		await page.screenshot({ path: testInfo.outputPath("reviewed-design-deliver.png") });
		const downloadPromise = page.waitForEvent("download");
		await exportDesign(page).click();
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

		await page.goto(`${root}/deliver/browser-tests`);
		await expect(page.getByRole("button", { name: "Run 0 Candidates", exact: true })).toBeDisabled();
		await expect(page.getByText("No preview yet. Preview execution readiness for the current test cases.", { exact: true })).toBeVisible();

		await page.goto(`${root}/reports?run=${historicalRun.run_record_id}`);
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

	test("opens declared setup and exact failed-run findings without dispatch", async ({ page }) => {
		const { errors, exportRequests, writes, historicalRun } = await setup(page);
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

		await page.goto(`${root}/deliver?run=${historicalRun.run_record_id}`);
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
		await page.goto(
			`${root}/inputs/context?finding_source=different-execution-snapshot&finding_run=${historicalRun.run_record_id}#quality-feedback-heading`
		);
		await expect(page.getByText("This saved run's quality findings are unavailable.", { exact: true })).toBeVisible();
		await expect(page.getByText(unrelatedFinding, { exact: true })).toHaveCount(0);
		await expect(page.getByRole("button", { name: "Review finding", exact: true })).toHaveCount(0);
		expect(writes).toEqual([]);
		expect(exportRequests).toEqual([]);
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
	expect(writes).toEqual([]);
	expect(errors).toEqual([]);
});
