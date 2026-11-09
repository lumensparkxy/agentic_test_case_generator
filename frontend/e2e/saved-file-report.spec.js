import { expect, test } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { expectNoDocumentOverflow } from "./support/layout.js";
import {
	createDeferred,
	installWorkspaceApi,
	projectDetailFixture,
	workspaceProjectFixture,
	workspaceReportFixture,
	workspaceSummaryFixture,
} from "./support/workspace.js";

const enabled = process.env.E2E_PROJECT_WORKSPACES_V2 === "true";
const project = workspaceProjectFixture({ project_id: "booking-report-evidence", name: "Booking report evidence", project_revision: 13 });
const root = `/projects/${project.project_id}`;
const priorSuite = "original-booking-suite";
const newerSuite = "new-booking-suite";
const priorReport = "saved-reviewed-booking-json";
const newerReport = "saved-reasoned-booking-csv";
const sparseReport = "older-booking-file";
const caseId = "TC-BOOKING";
const caseVersion = "original-booking-case-version";
const oldAssertion = "The original booking confirmation shows eight attendees.";
const draftReason = "Test data for the new capacity is unavailable.";
const surface = (page) => page.getByRole("region", { name: "Saved file report", exact: true });
const pathFor = (report, alias = enabled ? "deliver" : "reports") => `${root}/${alias}?report=${report}`;
const reports = {
	[priorReport]: {
		project_id: project.project_id,
		report_id: priorReport,
		format: "json",
		created_at: "2026-07-17T12:35:00Z",
		project_revision: 8,
		legacy_approved: false,
		delivery_kind: "reviewed_design",
		historical: true,
		stale: false,
		evidence: {
			source_snapshot_ids: { requirements: "booking-requirements-v1", use_cases: "booking-plan-v1", test_cases: priorSuite },
			suite_snapshot_id: priorSuite,
			exported_at: "2026-07-17T12:35:00Z",
			human_review_status: "unavailable",
			machine_review_status: "failed",
			execution_status: "blocked",
			execution_run_ids: ["original-browser-run"],
			requirement_ids: ["REQ-CAPACITY"],
			case_versions: [{ case_id: caseId, case_version_id: caseVersion, case_version_number: 1 }],
			design_delivery: {
				schema_version: 1,
				delivery_kind: "reviewed_design",
				legacy_approved: false,
				suite_snapshot_id: priorSuite,
				source_freshness: "current",
				design_status: "complete",
				scenario_decision: "approved",
				scenario_snapshot_id: "booking-plan-v1",
				scenario_review_id: "booking-plan-review-v1",
				human_acceptance: "unavailable",
				declared_setup: "blocked",
				required_requirement_ids: ["REQ-CAPACITY"],
				deferred_requirement_ids: ["REQ-DEFERRED"],
				excluded_requirement_ids: [],
				active_case_count: 1,
				deprecated_case_count: 0,
				target_readiness: "unknown",
			},
		},
	},
	[newerReport]: {
		project_id: project.project_id,
		report_id: newerReport,
		format: "csv",
		created_at: "2026-07-17T12:45:00Z",
		project_revision: 12,
		legacy_approved: false,
		delivery_kind: "draft",
		historical: false,
		stale: true,
		evidence: {
			source_snapshot_ids: { test_cases: newerSuite },
			suite_snapshot_id: newerSuite,
			exported_at: "2026-07-17T12:45:00Z",
			draft_override_reason: draftReason,
			human_review_status: "unavailable",
			machine_review_status: "failed",
			execution_status: "unknown",
			execution_run_ids: [],
			requirement_ids: [],
			case_versions: [{ case_id: caseId, case_version_id: "new-booking-version", case_version_number: 2 }],
		},
	},
	[sparseReport]: {
		project_id: project.project_id,
		report_id: sparseReport,
		format: "excel",
		created_at: "2026-07-17T11:35:00Z",
		project_revision: 4,
		legacy_approved: null,
		delivery_kind: "unknown",
		historical: true,
		stale: false,
		evidence: null,
	},
};
function summary(id) {
	const report = reports[id];
	return workspaceSummaryFixture({
		projects: [project],
		recent_reports: [
			workspaceReportFixture({
				report_id: id,
				project_id: project.project_id,
				project_name: project.name,
				format: report.format,
				operation: `export.${report.format}`,
				source_snapshot_id: report.evidence?.suite_snapshot_id,
				delivery_kind: report.delivery_kind === "reviewed_design" ? "reviewed_design" : null,
				approved: false,
			}),
		],
	});
}
async function setup(page, { failOnce = false, delayEarlier = false } = {}) {
	const writes = [];
	const reads = [];
	const errors = [];
	const earlierGate = createDeferred();
	page.on("pageerror", (error) => errors.push(error.message));
	page.on("request", (request) => {
		if (["fetch", "xhr"].includes(request.resourceType()) && !["GET", "HEAD", "OPTIONS"].includes(request.method()))
			writes.push(`${request.method()} ${new URL(request.url()).pathname}`);
	});
	await page.route("**/*", (route) => {
		if (!["fetch", "xhr"].includes(route.request().resourceType())) return route.fallback();
		return route.fulfill({ status: 404, json: { detail: "No fixture for this optional read." } });
	});
	const workspace = await installWorkspaceApi(page, {
		summary: summary(priorReport),
		projectDetails: {
			[project.project_id]: projectDetailFixture(project, {
				stage_state: { reports: { current_snapshot_id: newerReport, operation: "export.csv", stale: true } },
				current_snapshots: { reports: { snapshot_id: newerReport, stage: "reports", operation: "export.csv" } },
			}),
		},
	});
	await page.route(`**${root}/reports/*`, async (route) => {
		const request = route.request();
		const id = new URL(request.url()).pathname.split("/").at(-1);
		reads.push(id);
		expect(request.method()).toBe("GET");
		if (delayEarlier && id === priorReport) await earlierGate.promise;
		if (failOnce) {
			failOnce = false;
			return route.fulfill({ status: 503, json: { detail: "Evidence lookup temporarily failed." } });
		}
		if (!reports[id]) return route.fulfill({ status: 404 });
		try {
			await route.fulfill({ json: reports[id] });
		} catch (error) {
			if (!delayEarlier || id !== priorReport) throw error;
		}
	});
	await page.route(new RegExp(`${root}/test-case-evidence(?:\\?.*)?$`), (route) => {
		const { searchParams } = new URL(route.request().url());
		expect(searchParams.get("snapshot_id")).toBe(priorSuite);
		expect(searchParams.get("case_id")).toBe(caseId);
		return route.fulfill({
			json: {
				project_id: project.project_id,
				snapshot_id: priorSuite,
				suite_version: 1,
				project_revision: 7,
				historical: true,
				stale: false,
				test_case: {
					id: caseId,
					title: "Original booking test",
					artifact_version_id: caseVersion,
					steps: [{ step: 1, action: "Submit a booking with eight attendees.", expected: oldAssertion }],
				},
			},
		});
	});
	await seedAuthenticatedSession(page);
	return { workspace, writes, reads, errors, earlierGate };
}

test("opens the exact file from Reports and retains its original case after a newer export", { tag: "@p1" }, async ({ page }, testInfo) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	const api = await setup(page);
	await page.goto("/reports");
	const older = page.getByRole("link", { name: `Open report evidence for ${project.name}`, exact: true });
	await expect(older).toHaveAttribute("href", pathFor(priorReport));
	await older.click();
	await expect(page).toHaveURL(pathFor(priorReport));
	await expect(surface(page)).toContainText(priorReport);
	await expect(surface(page)).toContainText("Reviewed design");
	await expect(surface(page)).toContainText("Historical");
	await expect(surface(page).getByRole("heading", { name: "Saved file report", exact: true })).toBeFocused();
	await expect(page.getByRole("heading", { name: "Export Test Cases", exact: true })).toHaveCount(0);
	await expectNoDocumentOverflow(page);
	await surface(page).getByText("Original saved cases", { exact: true }).click();
	const link = surface(page).getByRole("link", { name: new RegExp(caseId) });
	await expect(link).toHaveAttribute("href", new RegExp(`[?&]suite=${priorSuite}&case=${caseId}$`));
	await page.screenshot({ path: testInfo.outputPath("historical-file-report.png") });
	await link.press("Enter");
	await expect(page.getByText(oldAssertion, { exact: true })).toBeVisible();
	await page.goBack();
	await expect(surface(page)).toContainText(priorReport);
	await surface(page).getByRole("link", { name: "Open current export", exact: true }).click();
	await expect(page.getByRole("heading", { name: "Export Test Cases", exact: true })).toBeVisible();
	await page.getByRole("link", { name: "Open latest saved file report", exact: true }).click();
	await expect(surface(page)).toContainText(newerReport);

	api.workspace.setSummary(summary(newerReport));
	await page.goto("/");
	const homeActivity = page
		.getByRole("link")
		.filter({ hasText: "View" })
		.and(page.locator(`a[href="${pathFor(newerReport)}"]`));
	await expect(homeActivity).toBeVisible();
	await homeActivity.click();
	await expect(surface(page)).toContainText(newerReport);
	await expect(surface(page)).toContainText(draftReason);
	await expect(surface(page)).toContainText("Stale");
	await page.goto(pathFor(priorReport, enabled ? "reports" : "deliver"));
	await expect(surface(page)).toContainText(priorReport);
	expect(api.writes).toEqual([]);
	expect(api.errors).toEqual([]);
});

test("retries only the same lookup and preserves a sparse older header", async ({ page }) => {
	const api = await setup(page, { failOnce: true });
	await page.goto(pathFor(sparseReport));
	await surface(page).getByRole("button", { name: "Retry saved report", exact: true }).click();
	await expect(surface(page)).toContainText(sparseReport);
	await expect(surface(page)).toContainText("Excel");
	await expect(surface(page).getByText("Original saved cases", { exact: true })).toHaveCount(0);
	expect(api.reads).toEqual([sparseReport, sparseReport]);
	await page.goto(`${pathFor(sparseReport)}&run=another-record`);
	await expect(surface(page)).toContainText("unavailable");
	await expect(page.getByRole("heading", { name: "Export Test Cases", exact: true })).toHaveCount(0);
	expect(api.reads).toEqual([sparseReport, sparseReport]);
	await page.goto(`${pathFor(sparseReport)}&report=${priorReport}`);
	await expect(surface(page)).toContainText("unavailable");
	expect(api.reads).toEqual([sparseReport, sparseReport]);
	expect(api.writes).toEqual([]);
});

test("ignores a late older report after navigation to a different exact file", async ({ page }) => {
	const api = await setup(page, { delayEarlier: true });
	await page.goto("/reports");
	const reportLink = page.getByRole("link", { name: `Open report evidence for ${project.name}`, exact: true });
	await reportLink.click();
	await expect.poll(() => api.reads.includes(priorReport)).toBe(true);
	api.workspace.setSummary(summary(newerReport));
	await page
		.getByRole("navigation", { name: "Global navigation", exact: true })
		.getByRole("link", { name: "Reports", exact: true })
		.click();
	await expect(reportLink).toHaveAttribute("href", pathFor(newerReport));
	await reportLink.click();
	await expect(surface(page)).toContainText(newerReport);
	api.earlierGate.resolve();
	await expect(surface(page)).toContainText(draftReason);
	await expect(surface(page)).not.toContainText(priorReport);
	expect(api.reads).toEqual([priorReport, newerReport]);
	expect(api.writes).toEqual([]);
	expect(api.errors).toEqual([]);
});
