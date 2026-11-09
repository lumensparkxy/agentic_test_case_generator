import { expect, test } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { expectVisuallyContained } from "./support/layout.js";
import { USE_CASE_PROJECT_ID, installUseCaseReviewApi, useCaseProjectFixture } from "./support/use-case-review.js";

const enabled = process.env.E2E_PROJECT_WORKSPACES_V2 === "true" && process.env.E2E_APPROVAL_CONTINUATION_ENABLED === "true";
const root = `/projects/${USE_CASE_PROJECT_ID}`;
const statusPath = `${root}/use-cases/refresh`;
const generatePath = `${root}/use-cases/generate`;
const previousPlanId = "booking-plan-capacity-8";
const refreshedPlanId = "booking-plan-capacity-6";
const oldSuiteId = "saved-booking-cases-capacity-8";
const changedIds = ["REQ-001", "REQ-002"];
const keptIds = ["REQ-003", "REQ-004", "REQ-005", "REQ-006", "REQ-007", "REQ-008", "REQ-009"];

function scenarioGroup(requirement, revised = false) {
	const number = requirement.id.slice(-3);
	return {
		requirement_id: requirement.id,
		requirement_text: requirement.text,
		scenarios: [
			{
				id: `${requirement.id}-SCN-01`,
				requirement_id: requirement.id,
				title: revised ? `Review capacity 6 scenario ${number}` : `Prior booking scenario ${number}`,
				objective: requirement.text,
				scenario_type: "Happy Path",
				priority: "Medium",
				must_have: true,
			},
		],
	};
}

async function setup(page, { loseReply = false, interrupted = false } = {}) {
	const project = useCaseProjectFixture({ reviewState: "approved", name: "Booking source change" });
	const plan = project.current_snapshots.use_cases;
	plan.snapshot_id = previousPlanId;
	plan.source_snapshot_id = "booking-requirements-capacity-8";
	plan.metadata = { source_requirements_snapshot_id: plan.source_snapshot_id, source_context_snapshot_id: "booking-context-old" };
	const source = project.current_snapshots.requirements;
	const before = [...changedIds, ...keptIds].map((id, index) => ({
		id,
		requirement_uid: `booking-${id}`,
		text: index < 2 ? `Booking rule ${index + 1} uses an attendee limit of 8.` : `Booking behavior ${index + 1} stays unchanged.`,
		review_status: "Approved",
		content_version: 1,
		lifecycle_status: "active",
	}));
	const after = before.map((row) =>
		changedIds.includes(row.id) ? { ...row, text: row.text.replace("limit of 8", "limit of 6"), content_version: 2 } : row
	);
	source.payload.requirements = after;
	source.operation = "requirements.review";
	plan.payload.coverage_plan = before.map((row) => scenarioGroup(row));
	plan.metadata.source_requirement_identities = Object.fromEntries(
		before.map(({ id, requirement_uid, content_version }) => [id, { requirement_uid, content_version }])
	);
	Object.assign(project.stage_state.use_cases, { current_snapshot_id: previousPlanId, stale: true, approved: true });
	project.stage_state.use_cases.metadata.latest_human_review.snapshot_id = previousPlanId;

	const testCase = {
		id: "TC-BOOKING-8",
		title: "Confirm a booking for eight attendees",
		artifact_version_id: "old-booking-artifact-version",
		linked_requirement_ids: [changedIds[0]],
		scenario_refs: [`${changedIds[0]}-SCN-01`],
		steps: [{ step: 1, action: "Book for eight attendees", expected: "The booking is confirmed for eight attendees." }],
	};
	project.current_snapshots.test_cases = {
		snapshot_id: oldSuiteId,
		project_id: project.project_id,
		project_revision: project.current_revision - 1,
		stage: "test_cases",
		version: 1,
		operation: "testcases.generate",
		source_snapshot_id: previousPlanId,
		approved: true,
		metadata: { source_use_case_snapshot_id: previousPlanId },
		payload: { test_cases: [testCase], review: { approved: true, score: 100, threshold: 90 }, approved: true },
	};
	project.stage_state.test_cases = { current_snapshot_id: oldSuiteId, version: 1, approved: true, stale: true, metadata: {} };
	const errors = [];
	const writes = [];
	const generationRequests = [];
	const statusReads = [];
	page.on("pageerror", (error) => errors.push(error.message));
	page.on("request", (request) => {
		if (["fetch", "xhr"].includes(request.resourceType()) && !["GET", "HEAD", "OPTIONS"].includes(request.method())) {
			writes.push(`${request.method()} ${new URL(request.url()).pathname}`);
		}
	});
	const api = await installUseCaseReviewApi(page, { initialProject: project });
	await page.route(new RegExp(`${root}/approval-continuations(?:\\?.*)?$`), (route) =>
		route.fulfill({ json: { enabled: true, tests_enabled: true, runs: [] } })
	);
	const operationId = `draft_${"c".repeat(64)}`;
	let attempt = interrupted
		? {
				operation_id: operationId,
				fence: 4,
				request_id: "scoped-booking-interrupted",
				state: "interrupted",
				reason: "provider_outcome_unconfirmed",
				source_snapshot_id: previousPlanId,
			}
		: null;
	let result = null;
	await page.route(new RegExp(`${statusPath}(?:\\?.*)?$`), (route) => {
		expect(route.request().method()).toBe("GET");
		const requestId = new URL(route.request().url()).searchParams.get("request_id");
		statusReads.push(requestId);
		return route.fulfill({
			json: {
				enabled: true,
				eligible: !result,
				blocked: Boolean(attempt),
				required_requirement_ids: result ? [] : changedIds,
				attempt,
				result: result?.request_id === requestId ? result : null,
			},
		});
	});
	await page.route(`**${statusPath}/${operationId}/release`, (route) => {
		expect(route.request().method()).toBe("POST");
		expect(route.request().postDataJSON()).toEqual({ fence: attempt.fence });
		attempt = null;
		return route.fulfill({ json: { released: true } });
	});
	await page.route(`**${generatePath}`, (route) => {
		const request = route.request();
		expect(request.method()).toBe("POST");
		const requestId = request.headers()["x-request-id"];
		const input = request.postDataJSON();
		expect(requestId).toBeTruthy();
		expect(input).toEqual({ base_project_revision: project.current_revision, refresh_requirement_ids: changedIds });
		generationRequests.push({ requestId, input });
		const next = api.getProject();
		next.current_revision++;
		const refresh = {
			previous_snapshot_id: previousPlanId,
			refreshed_requirement_ids: changedIds,
			retained_requirement_ids: keptIds,
			removed_requirement_ids: [],
		};
		next.current_snapshots.use_cases = {
			...plan,
			snapshot_id: refreshedPlanId,
			source_snapshot_id: source.snapshot_id,
			project_revision: next.current_revision,
			version: plan.version + 1,
			approved: false,
			metadata: { ...plan.metadata, source_requirements_snapshot_id: source.snapshot_id, use_case_refresh: refresh },
			payload: {
				...plan.payload,
				coverage_plan: plan.payload.coverage_plan.map((group, index) =>
					changedIds.includes(group.requirement_id) ? scenarioGroup(after[index], true) : group
				),
			},
		};
		next.stage_state.use_cases = {
			current_snapshot_id: refreshedPlanId,
			stale: false,
			approved: false,
			operation: "use_cases.generate",
			metadata: {},
			version: next.current_snapshots.use_cases.version,
		};
		result = { ...refresh, request_id: requestId, snapshot_id: refreshedPlanId, project_revision: next.current_revision };
		api.setProject(next);
		return loseReply ? route.abort("failed") : route.fulfill({ json: next });
	});
	await seedAuthenticatedSession(page);
	return { api, errors, generationRequests, oldSuite: project.current_snapshots.test_cases, statusReads, writes };
}

test.describe("Shared scoped scenario refresh", () => {
	test.skip(!enabled, "Requires the opt-in workspace and continuation");

	test("recovers the two changed groups and keeps the approval explicit", async ({ page }, testInfo) => {
		const { api, errors, generationRequests, oldSuite, statusReads, writes } = await setup(page, { loseReply: true });
		await page.setViewportSize({ width: 1440, height: 900 });
		await page.goto(`${root}/review`);
		await expect(page.getByRole("button", { name: "Approve scope and prepare scenarios", exact: true })).toHaveCount(0);
		const openRefresh = page.getByRole("button", { name: "Review refresh scope", exact: true });
		await expect(openRefresh).toBeEnabled();
		await openRefresh.click();
		const dialog = page.getByRole("dialog", { name: "Review Use Cases refresh scope", exact: true });
		await expect(dialog).toBeVisible();
		await expect(dialog.getByRole("status")).toContainText("2 changed · 0 added · 7 kept · 0 removed");
		await expect(dialog.getByRole("region", { name: "Changed requirements" })).toContainText("attendee limit of 6");
		await page.keyboard.press("Escape");
		await expect(dialog).toHaveCount(0);
		await expect(openRefresh).toBeFocused();
		await openRefresh.press("Enter");
		await expect(dialog).toBeVisible();
		await expectVisuallyContained(dialog.getByRole("button", { name: "Confirm scoped refresh", exact: true }), dialog);
		await page.screenshot({ path: testInfo.outputPath("scoped-booking-refresh-dialog.png") });
		expect(writes).toEqual([]);
		await dialog.getByRole("button", { name: "Confirm scoped refresh", exact: true }).click();

		await expect(page.getByText("Review capacity 6 scenario 001", { exact: true })).toBeVisible();
		await expect(page.getByText("Prior booking scenario 003", { exact: true })).toBeVisible();
		await expect(page.getByRole("status", { name: "Current human review status" })).toHaveText("Awaiting human review");
		await expect(page.getByText("Prior booking scenario 001", { exact: true })).toHaveCount(0);
		expect(api.getProject().current_snapshots.test_cases).toEqual(oldSuite);
		expect(api.getProject().stage_state.test_cases.stale).toBe(true);
		expect(generationRequests).toHaveLength(1);
		expect(statusReads).toContain(generationRequests[0].requestId);
		expect(writes).toEqual([`POST ${generatePath}`]);

		await page.getByRole("button", { name: "Approve all", exact: true }).click();
		const decision = page.getByRole("form", { name: "Human review decision", exact: true });
		await expect(decision.getByRole("button", { name: "Approve scenarios and generate tests", exact: true })).toHaveCount(0);
		const approve = decision.getByRole("button", { name: "Approve Use Cases", exact: true });
		await expect(approve).toBeEnabled();
		await approve.click();
		await expect(page.getByRole("status", { name: "Current human review status" })).toHaveText("Human approved");
		expect(api.requests.review[0].payload).toMatchObject({ snapshot_id: refreshedPlanId, decision: "approve" });
		expect(writes).toEqual([`POST ${generatePath}`, `POST ${root}/use-cases/reviews`]);
		expect(api.getProject().current_snapshots.test_cases).toEqual(oldSuite);
		expect(errors).toEqual([]);
	});

	test("requires explicit release of an interrupted refresh and keeps the prior plan", async ({ page }) => {
		const { api, errors, generationRequests, writes } = await setup(page, { interrupted: true });
		await page.goto(`${root}/review/scenarios`);
		const status = page.getByRole("region", { name: "Scenario refresh status", exact: true });
		const acknowledge = status.getByRole("button", { name: "Acknowledge interrupted scenario refresh", exact: true });
		await expect(acknowledge).toBeEnabled();
		expect(writes).toEqual([]);
		await acknowledge.click();
		await expect(acknowledge).toHaveCount(0);
		await expect(page.getByText("Prior booking scenario 001", { exact: true })).toBeVisible();
		expect(api.getProject().current_snapshots.use_cases.snapshot_id).toBe(previousPlanId);
		expect(generationRequests).toEqual([]);
		expect(writes).toEqual([`POST ${statusPath}/draft_${"c".repeat(64)}/release`]);
		expect(errors).toEqual([]);
	});
});
