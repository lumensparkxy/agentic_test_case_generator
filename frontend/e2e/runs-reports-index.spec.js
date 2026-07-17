import { expect, test } from "@playwright/test";

import { buildProjectPath } from "../src/app/workflowRoutes.js";
import { seedAuthenticatedSession } from "./support/auth.js";
import { expectNoDocumentOverflow, expectVisuallyContained } from "./support/layout.js";
import {
	createDeferred,
	installWorkspaceApi,
	workspaceProjectFixture,
	workspaceReportFixture,
	workspaceRunFixture,
	workspaceSummaryFixture,
} from "./support/workspace.js";

const MERCURY_PROJECT = workspaceProjectFixture({
	project_id: "activity-mercury",
	name: "Mercury Checkout",
	project_revision: 9,
	current_stage: "execution",
	current_status: "running",
	updated_at: "2026-07-17T12:40:00Z",
});

const ATLAS_PROJECT = workspaceProjectFixture({
	project_id: "activity-atlas",
	name: "Atlas Accounts",
	project_revision: 5,
	current_stage: "reports",
	current_status: "completed",
	updated_at: "2026-07-17T12:30:00Z",
});

const NOVA_PROJECT = workspaceProjectFixture({
	project_id: "activity-nova",
	name: "Nova Billing",
	project_revision: 12,
	current_stage: "reports",
	current_status: "failed",
	updated_at: "2026-07-17T12:20:00Z",
});

const ORBIT_PROJECT = workspaceProjectFixture({
	project_id: "activity-orbit",
	name: "Orbit Evidence",
	project_revision: 3,
	current_stage: "execution",
	current_status: "queued",
	updated_at: "2026-07-17T12:10:00Z",
});

const QUEUED_RUN = workspaceRunFixture({
	run_record_id: "run-record-orbit-queued",
	run_id: "run-orbit-queued",
	project_id: ORBIT_PROJECT.project_id,
	project_name: ORBIT_PROJECT.name,
	project_revision: ORBIT_PROJECT.project_revision,
	status: "queued",
	target_environment: "qa",
	selected_count: 7,
	executed_count: 0,
	passed_count: 0,
	failed_count: 0,
	invalid_count: 0,
	skipped_count: 0,
	updated_at: "2026-07-17T12:40:00Z",
});

const RUNNING_RUN = workspaceRunFixture({
	run_record_id: "run-record-mercury-running",
	run_id: "run-mercury-running",
	project_id: MERCURY_PROJECT.project_id,
	project_name: MERCURY_PROJECT.name,
	project_revision: MERCURY_PROJECT.project_revision,
	status: "running",
	target_environment: "production",
	selected_count: 8,
	executed_count: 3,
	passed_count: 1,
	failed_count: 1,
	invalid_count: 1,
	skipped_count: 0,
	updated_at: "2026-07-17T12:30:00Z",
});

const COMPLETED_RUN = workspaceRunFixture({
	run_record_id: "run-record-atlas-completed",
	run_id: "run-atlas-completed",
	project_id: ATLAS_PROJECT.project_id,
	project_name: ATLAS_PROJECT.name,
	project_revision: ATLAS_PROJECT.project_revision,
	status: "completed",
	target_environment: "staging",
	selected_count: 9,
	executed_count: 6,
	passed_count: 1,
	failed_count: 1,
	invalid_count: 1,
	skipped_count: 2,
	updated_at: "2026-07-17T12:20:00Z",
});

const FAILED_RUN = workspaceRunFixture({
	run_record_id: "run-record-nova-failed",
	run_id: "run-nova-failed",
	project_id: NOVA_PROJECT.project_id,
	project_name: NOVA_PROJECT.name,
	project_revision: NOVA_PROJECT.project_revision,
	status: "failed",
	target_environment: "local",
	selected_count: 4,
	executed_count: 2,
	passed_count: 0,
	failed_count: 2,
	invalid_count: 0,
	skipped_count: 0,
	updated_at: "2026-07-17T12:10:00Z",
});

const APPROVED_REPORT = workspaceReportFixture({
	report_id: "report-atlas-approved",
	project_id: ATLAS_PROJECT.project_id,
	project_name: ATLAS_PROJECT.name,
	project_revision: ATLAS_PROJECT.project_revision,
	status: "approved",
	report_type: "execution evidence",
	format: "pdf",
	operation: "export.pdf",
	approved: true,
	stale: false,
	count: 5,
	source_snapshot_id: "snapshot-atlas-evidence-v5",
	execution_run_ids: [COMPLETED_RUN.run_id],
	updated_at: "2026-07-17T12:35:00Z",
});

const DRAFT_REPORT = workspaceReportFixture({
	report_id: "report-mercury-draft",
	project_id: MERCURY_PROJECT.project_id,
	project_name: MERCURY_PROJECT.name,
	project_revision: MERCURY_PROJECT.project_revision,
	status: "draft",
	report_type: "coverage summary",
	format: "json",
	operation: "preview.json",
	approved: false,
	stale: false,
	count: 8,
	source_snapshot_id: "snapshot-mercury-evidence-v9",
	execution_run_ids: [RUNNING_RUN.run_id],
	updated_at: "2026-07-17T12:25:00Z",
});

const STALE_REPORT = workspaceReportFixture({
	report_id: "report-nova-stale",
	project_id: NOVA_PROJECT.project_id,
	project_name: NOVA_PROJECT.name,
	project_revision: NOVA_PROJECT.project_revision,
	status: "stale",
	report_type: "test suite",
	format: "csv",
	operation: "export.csv",
	approved: true,
	stale: true,
	count: 12,
	source_snapshot_id: "snapshot-nova-evidence-v11",
	execution_run_ids: [FAILED_RUN.run_id],
	updated_at: "2026-07-17T12:15:00Z",
});

const POPULATED_SUMMARY = workspaceSummaryFixture({
	projects: [MERCURY_PROJECT, ATLAS_PROJECT, NOVA_PROJECT, ORBIT_PROJECT],
	recent_runs: [QUEUED_RUN, RUNNING_RUN, COMPLETED_RUN, FAILED_RUN],
	recent_reports: [APPROVED_REPORT, DRAFT_REPORT, STALE_REPORT],
});

function activityMain(page, name) {
	return page.getByRole("main", { name: new RegExp(`^${name}$`, "i") });
}

function activityList(page, name) {
	return activityMain(page, name).getByRole("list", { name: new RegExp(`^Recent ${name.toLocaleLowerCase()}$`, "i") });
}

function activityRow(page, pageName, projectName) {
	return activityList(page, pageName).getByRole("listitem").filter({ hasText: projectName });
}

async function openActivityPage(page, path, options = {}) {
	const api = await installWorkspaceApi(page, options);
	await seedAuthenticatedSession(page);
	await page.goto(path);
	await expect(page.getByRole("heading", { name: new RegExp(`^${path.slice(1)}$`, "i"), level: 1 })).toBeVisible({ timeout: 30_000 });
	return api;
}

async function expectNoProjectShell(page) {
	await expect(page.getByRole("navigation", { name: /^Project navigation$/i })).toHaveCount(0);
	await expect(page.getByLabel("Project information rail")).toHaveCount(0);
	await expect(page.getByLabel("Contextual task")).toHaveCount(0);
}

async function expectStatusWithIcon(row, value, label) {
	const status = row.locator(`.activity-index-status[data-status-value="${value}"]`);
	await expect(status).toBeVisible();
	await expect(status).toContainText(label);
	await expect(status.locator("svg")).toBeVisible();
}

test.describe("Global Runs and Reports indexes", () => {
	test("renders authoritative run identities, durable states, exact totals, and canonical evidence links", async ({ page }) => {
		const api = await openActivityPage(page, "/runs", { summary: POPULATED_SUMMARY });
		const heading = page.getByRole("heading", { name: /^Runs$/i, level: 1 });
		await expect(heading).toBeFocused();
		await expect(page.getByRole("navigation", { name: /^Global navigation$/i }).getByRole("link", { name: /^Runs$/i })).toHaveAttribute(
			"aria-current",
			"page"
		);
		await expectNoProjectShell(page);

		const rows = activityList(page, "Runs").getByRole("listitem");
		await expect(rows).toHaveCount(4);
		await expect(rows.nth(0)).toContainText(ORBIT_PROJECT.name);
		await expect(rows.nth(1)).toContainText(MERCURY_PROJECT.name);
		await expect(rows.nth(2)).toContainText(ATLAS_PROJECT.name);
		await expect(rows.nth(3)).toContainText(NOVA_PROJECT.name);

		const queuedRow = activityRow(page, "Runs", ORBIT_PROJECT.name);
		await expectStatusWithIcon(queuedRow, "queued", "Queued");
		await expect(queuedRow).toContainText(QUEUED_RUN.run_id);
		await expect(queuedRow).toContainText(/QA/i);
		await expect(queuedRow).toContainText(/Selected\s*7/i);
		await expect(queuedRow).toContainText(/Executed\s*0/i);

		const runningRow = activityRow(page, "Runs", MERCURY_PROJECT.name);
		await expectStatusWithIcon(runningRow, "running", "Running");
		await expect(runningRow).toContainText(RUNNING_RUN.run_id);
		await expect(runningRow).toContainText(/Production/i);
		await expect(runningRow).toContainText(/Selected\s*8/i);
		await expect(runningRow).toContainText(/Executed\s*3/i);
		await expect(runningRow).toContainText(/Passed\s*1/i);
		await expect(runningRow).toContainText(/Failed\s*1/i);
		await expect(runningRow).toContainText(/Invalid\s*1/i);

		const completedRow = activityRow(page, "Runs", ATLAS_PROJECT.name);
		await expectStatusWithIcon(completedRow, "completed", "Completed");
		await expect(completedRow).toContainText(COMPLETED_RUN.run_id);
		await expect(completedRow).toContainText(/Selected\s*9/i);
		await expect(completedRow).toContainText(/Executed\s*6/i);
		await expect(completedRow).toContainText(/Passed\s*1/i);
		await expect(completedRow).toContainText(/Failed\s*1/i);
		await expect(completedRow).toContainText(/Invalid\s*1/i);

		const failedRow = activityRow(page, "Runs", NOVA_PROJECT.name);
		await expectStatusWithIcon(failedRow, "failed", "Failed");
		await expect(failedRow).toContainText(FAILED_RUN.run_id);
		await expect(failedRow).toContainText(/Selected\s*4/i);
		await expect(failedRow).toContainText(/Executed\s*2/i);
		await expect(failedRow).toContainText(/Failed\s*2/i);

		for (const [project, row] of [
			[ORBIT_PROJECT, queuedRow],
			[MERCURY_PROJECT, runningRow],
			[ATLAS_PROJECT, completedRow],
			[NOVA_PROJECT, failedRow],
		]) {
			await expect(row.getByRole("link", { name: `Open automation evidence for ${project.name}` })).toHaveAttribute(
				"href",
				buildProjectPath(project.project_id, "automation")
			);
		}
		for (const run of [QUEUED_RUN, RUNNING_RUN, COMPLETED_RUN, FAILED_RUN]) {
			await expect(activityRow(page, "Runs", run.project_name).getByRole("time")).toHaveAttribute("datetime", run.updated_at);
		}

		expect(api.requests.workspaceSummary).toHaveLength(1);
		expect(api.requests.projectDetail).toHaveLength(0);
	});

	test("renders report evidence identity with text-and-icon approval, draft, and stale states", async ({ page }) => {
		const api = await openActivityPage(page, "/reports", { summary: POPULATED_SUMMARY });
		const heading = page.getByRole("heading", { name: /^Reports$/i, level: 1 });
		await expect(heading).toBeFocused();
		await expect(page.getByRole("navigation", { name: /^Global navigation$/i }).getByRole("link", { name: /^Reports$/i })).toHaveAttribute(
			"aria-current",
			"page"
		);
		await expectNoProjectShell(page);

		const rows = activityList(page, "Reports").getByRole("listitem");
		await expect(rows).toHaveCount(3);
		await expect(rows.nth(0)).toContainText(ATLAS_PROJECT.name);
		await expect(rows.nth(1)).toContainText(MERCURY_PROJECT.name);
		await expect(rows.nth(2)).toContainText(NOVA_PROJECT.name);

		for (const [report, project, status] of [
			[APPROVED_REPORT, ATLAS_PROJECT, "Approved"],
			[DRAFT_REPORT, MERCURY_PROJECT, "Draft"],
			[STALE_REPORT, NOVA_PROJECT, "Stale"],
		]) {
			const row = activityRow(page, "Reports", project.name);
			await expectStatusWithIcon(row, status.toLocaleLowerCase(), status);
			await expect(row).toContainText(report.report_id);
			await expect(row).toContainText(new RegExp(report.report_type, "i"));
			await expect(row).toContainText(new RegExp(report.format, "i"));
			await expect(row).toContainText(new RegExp(`Evidence items\\s*${report.count}`, "i"));
			await expect(row.getByRole("link", { name: `Open report evidence for ${project.name}` })).toHaveAttribute(
				"href",
				buildProjectPath(project.project_id, "reports")
			);
			await expect(row.getByRole("time")).toHaveAttribute("datetime", report.updated_at);
		}

		expect(api.requests.workspaceSummary).toHaveLength(1);
		expect(api.requests.projectDetail).toHaveLength(0);
	});

	test("filters runs locally, exposes a distinct filtered-empty state, and never mutates evidence", async ({ page }) => {
		const mutationRequests = [];
		page.on("request", (request) => {
			if (["fetch", "xhr"].includes(request.resourceType()) && request.method() !== "GET") mutationRequests.push(request);
		});
		const api = await openActivityPage(page, "/runs", { summary: POPULATED_SUMMARY });
		const main = activityMain(page, "Runs");
		const search = main.getByRole("searchbox", { name: /^Search runs$/i });
		const status = main.getByRole("combobox", { name: /^Status$/i });
		const environment = main.getByRole("combobox", { name: /^Environment$/i });

		await search.fill("Mercury");
		await expect(search).toBeFocused();
		await expect(activityList(page, "Runs").getByRole("listitem")).toHaveCount(1);
		await expect(activityMain(page, "Runs")).toContainText(MERCURY_PROJECT.name);

		await search.fill("");
		await status.focus();
		await status.selectOption("running");
		await expect(status).toBeFocused();
		await expect(activityList(page, "Runs").getByRole("listitem")).toHaveCount(1);
		await environment.focus();
		await environment.selectOption("staging");
		await expect(environment).toBeFocused();
		await expect(main.getByRole("heading", { name: /^No runs match these filters$/i })).toBeVisible();

		await main.getByRole("button", { name: /^Clear filters$/i }).click();
		await expect(search).toBeFocused();
		await expect(activityList(page, "Runs").getByRole("listitem")).toHaveCount(4);
		expect(api.requests.workspaceSummary).toHaveLength(1);
		expect(api.requests.projectDetail).toHaveLength(0);
		expect(mutationRequests).toHaveLength(0);
	});

	test("filters reports locally across status, type, and format without requesting or changing evidence", async ({ page }) => {
		const mutationRequests = [];
		page.on("request", (request) => {
			if (["fetch", "xhr"].includes(request.resourceType()) && request.method() !== "GET") mutationRequests.push(request);
		});
		const api = await openActivityPage(page, "/reports", { summary: POPULATED_SUMMARY });
		const main = activityMain(page, "Reports");
		const search = main.getByRole("searchbox", { name: /^Search reports$/i });
		const status = main.getByRole("combobox", { name: /^Status$/i });
		const type = main.getByRole("combobox", { name: /^Type$/i });
		const format = main.getByRole("combobox", { name: /^Format$/i });

		await search.fill("Nova");
		await expect(search).toBeFocused();
		await expect(activityList(page, "Reports").getByRole("listitem")).toHaveCount(1);
		await expect(main).toContainText(STALE_REPORT.report_id);

		await search.fill("");
		await type.focus();
		await type.selectOption(DRAFT_REPORT.report_type);
		await expect(type).toBeFocused();
		await expect(activityList(page, "Reports").getByRole("listitem")).toHaveCount(1);
		await expect(main).toContainText(DRAFT_REPORT.report_id);

		await type.selectOption("all");
		await status.focus();
		await status.selectOption("approved");
		await expect(status).toBeFocused();
		await expect(activityList(page, "Reports").getByRole("listitem")).toHaveCount(1);
		await format.focus();
		await format.selectOption("csv");
		await expect(format).toBeFocused();
		await expect(main.getByRole("heading", { name: /^No reports match these filters$/i })).toBeVisible();

		await status.selectOption("all");
		await format.selectOption("all");
		await search.fill(DRAFT_REPORT.source_snapshot_id);
		await expect(main.getByRole("heading", { name: /^No reports match these filters$/i })).toBeVisible();

		await main.getByRole("button", { name: /^Clear filters$/i }).click();
		await expect(search).toBeFocused();
		await expect(activityList(page, "Reports").getByRole("listitem")).toHaveCount(3);
		expect(api.requests.workspaceSummary).toHaveLength(1);
		expect(api.requests.projectDetail).toHaveLength(0);
		expect(mutationRequests).toHaveLength(0);
	});

	for (const destination of ["runs", "reports"]) {
		test(`announces bounded loading and then renders the ${destination} index`, async ({ page }) => {
			const summaryGate = createDeferred();
			const api = await installWorkspaceApi(page, {
				summary: POPULATED_SUMMARY,
				summaryScenarios: [{ gate: summaryGate.promise, payload: POPULATED_SUMMARY }],
			});
			await seedAuthenticatedSession(page);
			await page.goto(`/${destination}`);

			const heading = page.getByRole("heading", { name: new RegExp(`^${destination}$`, "i"), level: 1 });
			await expect(heading).toBeVisible({ timeout: 30_000 });
			await expect(heading).toBeFocused();
			await expect(page.getByRole("status", { name: /^Loading workspace$/i })).toBeVisible();
			await expect.poll(() => api.requests.workspaceSummary.length).toBe(1);

			summaryGate.resolve();
			await expect(activityList(page, destination === "runs" ? "Runs" : "Reports")).toBeVisible();
			await expect(page.getByRole("status", { name: /^Loading workspace$/i })).toHaveCount(0);
		});
	}

	test("distinguishes true-empty indexes from filtered-empty results", async ({ page }) => {
		await openActivityPage(page, "/runs", { summary: workspaceSummaryFixture() });
		await expect(activityMain(page, "Runs").getByRole("heading", { name: /^No recent runs$/i })).toBeVisible();
		await expect(activityMain(page, "Runs").getByRole("list")).toHaveCount(0);

		await page
			.getByRole("navigation", { name: /^Global navigation$/i })
			.getByRole("link", { name: /^Reports$/i })
			.click();
		await expect(page).toHaveURL(/\/reports\/?$/);
		await expect(page.getByRole("heading", { name: /^Reports$/i, level: 1 })).toBeFocused();
		await expect(activityMain(page, "Reports").getByRole("heading", { name: /^No recent reports$/i })).toBeVisible();
		await expect(activityMain(page, "Reports").getByRole("list")).toHaveCount(0);
	});

	for (const destination of ["runs", "reports"]) {
		test(`recovers the ${destination} index from a cold summary failure through Retry`, async ({ page }) => {
			const api = await openActivityPage(page, `/${destination}`, {
				summary: POPULATED_SUMMARY,
				summaryScenarios: [
					{ status: 503, payload: { detail: `${destination} activity is temporarily unavailable` } },
					{ payload: POPULATED_SUMMARY },
				],
			});

			const alert = page.getByRole("alert");
			await expect(alert).toContainText(`${destination} activity is temporarily unavailable`, { ignoreCase: true });
			await alert.getByRole("button", { name: /^Retry$/i }).click();
			await expect.poll(() => api.requests.workspaceSummary.length).toBe(2);
			await expect(alert).toHaveCount(0);
			await expect(activityList(page, destination === "runs" ? "Runs" : "Reports")).toBeVisible();
			await expect(page.getByRole("heading", { name: new RegExp(`^${destination}$`, "i"), level: 1 })).toBeFocused();
		});
	}

	test("contains long run and report evidence across compact and wide workspace widths", async ({ page }) => {
		const longProjectName = `Long evidence project ${"continuouslylong".repeat(9)}`;
		const project = workspaceProjectFixture({
			project_id: "activity-long-evidence",
			name: longProjectName,
			current_stage: "reports",
			current_status: "stale",
		});
		const longRun = workspaceRunFixture({
			run_record_id: `run-record-${"unbroken".repeat(14)}`,
			run_id: `run-${"unbroken".repeat(14)}`,
			project_id: project.project_id,
			project_name: project.name,
			status: `provider_${"still_running_".repeat(7)}state`,
			target_environment: `environment-${"unbroken".repeat(12)}`,
		});
		const longReport = workspaceReportFixture({
			report_id: `report-${"unbroken".repeat(16)}`,
			project_id: project.project_id,
			project_name: project.name,
			status: "stale",
			report_type: `evidence_${"long_type_".repeat(10)}`,
			format: `format-${"unbroken".repeat(10)}`,
		});
		await installWorkspaceApi(page, {
			summary: workspaceSummaryFixture({ projects: [project], recent_runs: [longRun], recent_reports: [longReport] }),
		});
		await seedAuthenticatedSession(page);

		for (const width of [320, 390, 900, 1280, 1920]) {
			await page.setViewportSize({ width, height: 1000 });
			for (const destination of ["runs", "reports"]) {
				await page.goto(`/${destination}`);
				const main = activityMain(page, destination === "runs" ? "Runs" : "Reports");
				const row = main.getByRole("listitem");
				await expect(row).toBeVisible();
				await expectVisuallyContained(row.locator(".activity-index-status"), row);
				await expectVisuallyContained(row.getByRole("link"), row);
				await expectNoDocumentOverflow(page, `${width}px ${destination} activity index`);
			}
		}
	});
});
