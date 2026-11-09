import { expect, test } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { expectNoDocumentOverflow } from "./support/layout.js";
import { USE_CASE_PROJECT_ID, installUseCaseReviewApi, useCaseProjectFixture, useCaseSnapshotFixture } from "./support/use-case-review.js";

const root = `/projects/${USE_CASE_PROJECT_ID}`;
const changeId = "booking-material-correction";
const originalPlanId = "provisional-booking-plan";
const originalSuiteId = "provisional-booking-suite";
const reboundPlanId = "rebound-booking-plan";
const reboundSuiteId = "rebound-booking-suite";
const admissionPath = `${root}/changes/admission`;
const rebindPath = `${root}/changes/rebind-drafts`;
const reviewRequirements = `${root}/requirements/reviews`;
const reviewPlan = `${root}/use-cases/reviews`;
const rebind = (page) => page.getByRole("region", { name: "Project Sync draft rebind", exact: true });
const reviewSaved = (page) => page.getByRole("button", { name: "Review saved suite", exact: true });

function setCurrent(project, snapshot, { stale = false } = {}) {
	project.current_snapshots[snapshot.stage] = snapshot;
	project.stage_state[snapshot.stage] = {
		...project.stage_state[snapshot.stage],
		current_snapshot_id: snapshot.snapshot_id,
		version: snapshot.version,
		operation: snapshot.operation,
		approved: snapshot.approved,
		source_snapshot_id: snapshot.source_snapshot_id,
		stale,
		metadata: snapshot.metadata,
	};
}

async function setup(page, { loseRebindReply = false } = {}) {
	const project = useCaseProjectFixture({
		name: "Booking capacity correction",
		snapshot: useCaseSnapshotFixture({ snapshot_id: originalPlanId, operation: "project.sync.draft" }),
	});
	const requirements = project.current_snapshots.requirements;
	requirements.operation = "quality.feedback.apply";
	requirements.approved = false;
	requirements.metadata = { change_id: changeId };
	requirements.payload.requirements = [
		{
			id: "REQ-101",
			requirement_uid: "booking-capacity",
			text: "Allow up to six attendees.",
			review_status: "Needs Review",
			content_version: 2,
		},
		{ id: "REQ-202", requirement_uid: "booking-receipt", text: "Show the booking receipt.", review_status: "Approved", content_version: 1 },
	];
	setCurrent(project, requirements);
	setCurrent(project, {
		project_id: project.project_id,
		stage: "context",
		snapshot_id: "booking-corrected-context",
		version: 2,
		project_revision: project.current_revision,
		operation: "quality.feedback.apply",
		approved: false,
		metadata: { change_id: changeId },
		payload: { requirements: requirements.payload.requirements },
	});
	const cases = [
		{
			id: "TC-RECEIPT",
			title: "Read the receipt number",
			artifact_version_id: "retained-booking-version",
			linked_requirement_ids: ["REQ-202"],
			scenario_refs: ["REQ-202-SCN-01"],
			steps: [{ step: 1, action: "Read the confirmation", expected: "The booking receipt number appears.", test_data: null }],
		},
		{
			id: "TC-CAPACITY",
			title: "Submit a booking for six attendees",
			artifact_version_id: "six-attendees-version",
			linked_requirement_ids: ["REQ-101"],
			scenario_refs: ["REQ-101-SCN-01"],
			steps: [{ step: 1, action: "Book for six attendees", expected: "Six attendees are shown on the booking.", test_data: "  6  " }],
		},
	];
	const suite = {
		project_id: project.project_id,
		stage: "test_cases",
		snapshot_id: originalSuiteId,
		version: 2,
		project_revision: project.current_revision,
		operation: "project.sync.draft",
		source_snapshot_id: originalPlanId,
		approved: false,
		metadata: { change_id: changeId },
		payload: { test_cases: cases, approved: false, substantive_assessment: { status: "unknown" } },
	};
	setCurrent(project, suite);
	project.material_requirements_review_required = true;
	project.project_sync = {
		id: changeId,
		status: "ready_for_review",
		stages: { requirements: "draft", context: "draft", use_cases: "draft", test_cases: "draft", automation: "not_requested" },
	};
	let state = "needs_requirement_approval";
	let savedReceipt = null;
	let skipReceiptOnce = false;
	const writes = [];
	const statusReads = [];
	const errors = [];
	page.on("pageerror", (error) => errors.push(error.message));
	page.on("request", (request) => {
		if (["fetch", "xhr"].includes(request.resourceType()) && !["GET", "HEAD", "OPTIONS"].includes(request.method())) {
			writes.push(`${request.method()} ${new URL(request.url()).pathname}`);
		}
	});
	await page.route("**/*", (route) => {
		if (!["fetch", "xhr"].includes(route.request().resourceType())) return route.fallback();
		return route.fulfill({ status: 404, json: { detail: "No fixture for this optional read." } });
	});
	await page.route(`**${root}/requirement-imports`, (route) => route.fulfill({ json: [] }));
	await page.route(`**${root}/knowledge`, (route) => route.fulfill({ json: { entries: [], skills: [], features: {}, revision: "0" } }));
	const api = await installUseCaseReviewApi(page, {
		initialProject: project,
		reviewScenarios: [
			{
				serverProject: (saved, payload) => {
					expect(payload).toMatchObject({ snapshot_id: reboundPlanId, decision: "approve" });
					saved.current_snapshots.test_cases.metadata.review_retry_available = true;
					return saved;
				},
			},
		],
	});
	await page.route(`**${root}/quality-findings`, (route) =>
		route.fulfill({ json: { findings: [], project_revision: api.getProject().current_revision } })
	);
	await page.route(new RegExp(`${root}/approval-continuations(?:\\?.*)?$`), (route) =>
		route.fulfill({ json: { enabled: true, tests_enabled: true, saved_review_enabled: true, runs: [] } })
	);
	await page.route(new RegExp(`${root}/test-cases/repair(?:\\?.*)?$`), (route) =>
		route.fulfill({ json: { enabled: true, blocked: false, attempt: null, target: null } })
	);
	await page.route(new RegExp(`${admissionPath}(?:\\?.*)?$`), (route) => {
		const id = new URL(route.request().url()).searchParams.get("request_id");
		statusReads.push(id);
		if (id && skipReceiptOnce) {
			skipReceiptOnce = false;
			return route.fulfill({ status: 503, json: { detail: "Synthetic status unavailable." } });
		}
		return route.fulfill({
			json: {
				enabled: true,
				blocked: false,
				attempt: null,
				rebind: { change_id: changeId, state },
				rebind_receipt: id && savedReceipt?.request_id === id ? savedReceipt : null,
			},
		});
	});
	await page.route(`**${reviewRequirements}`, (route) => {
		const saved = api.getProject();
		const { base_project_revision: revision, reviews } = route.request().postDataJSON();
		expect(revision).toBe(saved.current_revision);
		expect(reviews).toEqual(
			["booking-capacity", "booking-receipt"].map((requirement_uid) => ({ requirement_uid, review_status: "Approved", quality_flags: [] }))
		);
		saved.current_revision++;
		setCurrent(saved, {
			...saved.current_snapshots.requirements,
			snapshot_id: "approved-booking-correction",
			project_revision: saved.current_revision,
			operation: "requirements.review",
			approved: true,
			payload: {
				requirements: saved.current_snapshots.requirements.payload.requirements.map((row) => ({ ...row, review_status: "Approved" })),
			},
		});
		saved.stage_state.use_cases.stale = true;
		saved.stage_state.test_cases.stale = true;
		saved.material_requirements_review_required = false;
		saved.material_scope_pending_rebind = true;
		state = "available";
		api.setProject(saved);
		return route.fulfill({ json: saved });
	});
	await page.route(`**${rebindPath}`, (route) => {
		const request = route.request();
		const saved = api.getProject();
		const requestId = request.headers()["x-request-id"];
		expect(requestId).toBeTruthy();
		expect(request.postDataJSON()).toEqual({ change_id: changeId, base_project_revision: saved.current_revision });
		const revision = saved.current_revision;
		saved.current_revision++;
		setCurrent(saved, {
			...saved.current_snapshots.use_cases,
			snapshot_id: reboundPlanId,
			project_revision: saved.current_revision,
			operation: "project.sync.rebind",
			source_snapshot_id: saved.current_snapshots.requirements.snapshot_id,
			metadata: { change_id: changeId, previous_snapshot_id: originalPlanId, rebind_receipt_id: requestId },
		});
		setCurrent(saved, {
			...saved.current_snapshots.test_cases,
			snapshot_id: reboundSuiteId,
			project_revision: saved.current_revision,
			operation: "project.sync.rebind",
			source_snapshot_id: reboundPlanId,
			metadata: { change_id: changeId, previous_snapshot_id: originalSuiteId, rebind_receipt_id: requestId },
		});
		state = "bound";
		savedReceipt = {
			request_id: requestId,
			change_id: changeId,
			base_project_revision: revision,
			project_revision: saved.current_revision,
			requirements_snapshot_id: saved.current_snapshots.requirements.snapshot_id,
			context_snapshot_id: saved.current_snapshots.context.snapshot_id,
			previous_use_cases_snapshot_id: originalPlanId,
			previous_test_cases_snapshot_id: originalSuiteId,
			use_cases_snapshot_id: reboundPlanId,
			test_cases_snapshot_id: reboundSuiteId,
		};
		saved.material_scope_pending_rebind = false;
		saved.project_sync.review_rebind = savedReceipt;
		api.setProject(saved);
		if (loseRebindReply) {
			skipReceiptOnce = true;
			return route.abort("failed");
		}
		return route.fulfill({ json: savedReceipt });
	});
	await seedAuthenticatedSession(page);
	return { api, cases, errors, statusReads, writes, receipt: () => savedReceipt };
}

test.describe("Rebind exact material Project Sync drafts", () => {
	test.skip(
		process.env.E2E_PROJECT_WORKSPACES_V2 !== "true" || process.env.E2E_APPROVAL_CONTINUATION_ENABLED !== "true",
		"Requires both opt-in project workspace flags"
	);

	test("approves the corrected scope and recovers the exact saved rebind before approving its scenario plan", async ({
		page,
	}, testInfo) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		const fixture = await setup(page, { loseRebindReply: true });
		await page.goto(`${root}/review/tests`);
		await expect(rebind(page)).toContainText("Corrected requirements need approval");
		await expect(reviewSaved(page)).toHaveCount(0);
		await rebind(page).getByRole("link", { name: "Review corrected requirements", exact: true }).press("Enter");
		await expect(page).toHaveURL(`${root}/review`);
		await expect(page.getByRole("button", { name: "Approve scope and prepare scenarios", exact: true })).toHaveCount(0);
		expect(fixture.writes).toEqual([]);
		await page.getByText("Review actions", { exact: true }).click();
		await page.getByRole("button", { name: "Approve eligible requirements", exact: true }).click();
		await expect(rebind(page)).toContainText("Approved correction ready to rebind");
		expect(fixture.writes).toEqual([`PATCH ${reviewRequirements}`]);
		await expectNoDocumentOverflow(page);
		await page.screenshot({ path: testInfo.outputPath("material-draft-rebind-ready.png") });

		await rebind(page).getByRole("button", { name: "Rebind saved drafts", exact: true }).click();
		await expect(rebind(page).getByRole("button", { name: "Check rebind status", exact: true })).toBeVisible();
		await expect.poll(() => fixture.statusReads.includes(fixture.receipt()?.request_id)).toBe(true);
		await page.reload();
		await expect(rebind(page)).toContainText("Drafts rebound to the approved requirements");
		expect(fixture.writes).toEqual([`PATCH ${reviewRequirements}`, `POST ${rebindPath}`]);
		expect(fixture.api.getProject().current_snapshots.test_cases.payload.test_cases).toEqual(fixture.cases);
		const original = rebind(page).getByRole("link", { name: "Open original saved case", exact: true });
		await expect(original).toHaveAttribute("href", new RegExp(`[?&]suite=${originalSuiteId}&case=TC-RECEIPT$`));
		await expect(page.getByRole("heading", { name: "Requirement Review Workbench", exact: true })).toBeVisible();
		await page.screenshot({ path: testInfo.outputPath("material-draft-rebind-recovered.png") });

		await rebind(page).getByRole("link", { name: "Review rebound scenarios", exact: true }).press("Enter");
		await expect(page).toHaveURL(`${root}/review/scenarios`);
		await expect(page.getByRole("status", { name: "Current human review status" })).toHaveText("Awaiting human review");
		await page.getByRole("button", { name: "Approve all", exact: true }).click();
		const decision = page.getByRole("form", { name: "Human review decision", exact: true });
		await expect(decision.getByRole("button", { name: "Approve scenarios and generate tests", exact: true })).toHaveCount(0);
		await decision.getByRole("button", { name: "Approve Use Cases", exact: true }).click();
		await expect(page.getByRole("status", { name: "Current human review status" })).toHaveText("Human approved");
		await page.goto(`${root}/review/tests`);
		await expect(reviewSaved(page)).toBeEnabled();
		expect(fixture.writes).toEqual([`PATCH ${reviewRequirements}`, `POST ${rebindPath}`, `POST ${reviewPlan}`]);
		expect(fixture.errors).toEqual([]);
	});
});
