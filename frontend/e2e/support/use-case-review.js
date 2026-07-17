import { TEST_USER_ID, workspaceProjectFixture, workspaceSummaryFixture, workspaceWorkItemFixture } from "./workspace.js";

export const USE_CASE_PROJECT_ID = "project-use-case-review";
export const USE_CASE_SNAPSHOT_ID = "snapshot-use-cases-v3";
export const USE_CASE_BASE_REVISION = 7;
export const USE_CASE_GENERATED_AT = "2026-07-17T10:15:00Z";
export const USE_CASE_DECIDED_AT = "2026-07-17T12:45:00Z";

const clone = (value) => JSON.parse(JSON.stringify(value));

export function useCaseCoveragePlanFixture() {
	return [
		{
			requirement_id: "REQ-101",
			requirement_text: "Customers can complete express checkout with a saved payment method.",
			scenarios: [
				{
					id: "REQ-101-SCN-01",
					requirement_id: "REQ-101",
					scenario_type: "Happy Path",
					title: "Complete express checkout",
					objective: "Confirm a signed-in customer can pay with a valid saved card.",
					priority: "Critical",
					must_have: true,
				},
				{
					id: "REQ-101-SCN-02",
					requirement_id: "REQ-101",
					scenario_type: "Negative",
					title: "Recover from a declined saved card",
					objective: "Explain the decline and preserve the cart so another payment method can be selected.",
					priority: "High",
					must_have: true,
				},
			],
		},
		{
			requirement_id: "REQ-202",
			requirement_text: "High-value purchases require approval from a checkout supervisor.",
			scenarios: [
				{
					id: "REQ-202-SCN-01",
					requirement_id: "REQ-202",
					scenario_type: "Authorization",
					title: "Require supervisor approval",
					objective: "Prevent an unauthorized operator from approving a high-value purchase.",
					priority: "Critical",
					must_have: true,
				},
				{
					id: "REQ-202-SCN-02",
					requirement_id: "REQ-202",
					scenario_type: "Boundary",
					title: "Allow a purchase exactly at the approval threshold",
					objective: "Verify the configured threshold has an unambiguous inclusive boundary.",
					priority: "Medium",
					must_have: false,
				},
			],
		},
	];
}

export function useCaseRequirementAnalysisFixture() {
	return [
		{
			requirement_id: "REQ-101",
			requirement_text: "Customers can complete express checkout with a saved payment method.",
			business_rules: [
				{
					id: "RULE-101",
					requirement_id: "REQ-101",
					title: "Preserve the cart after payment failure",
					description: "A declined payment must not clear an active cart.",
					rule_type: "Business",
				},
			],
			field_constraints: [
				{
					id: "CONSTRAINT-101",
					requirement_id: "REQ-101",
					field_name: "saved_payment_method",
					description: "The saved card must be active and belong to the signed-in customer.",
					constraint_type: "Dependency",
				},
			],
			role_permissions: [],
			state_transitions: [],
			risk_signals: [
				{
					id: "RISK-101",
					requirement_id: "REQ-101",
					title: "Duplicate charge after retry",
					rationale: "A retry must use an idempotent payment identity.",
					category: "Data Integrity",
					severity: "High",
				},
			],
			suggested_scenarios: ["Expired saved card"],
			dependencies: ["Payment gateway availability"],
		},
		{
			requirement_id: "REQ-202",
			requirement_text: "High-value purchases require approval from a checkout supervisor.",
			business_rules: [],
			field_constraints: [
				{
					id: "CONSTRAINT-202",
					requirement_id: "REQ-202",
					field_name: "approval_threshold",
					description: "The threshold is configured in account currency.",
					constraint_type: "Range",
				},
			],
			role_permissions: [
				{
					id: "PERMISSION-202",
					requirement_id: "REQ-202",
					role: "Checkout supervisor",
					action: "Approve high-value purchase",
					effect: "Allow",
				},
			],
			state_transitions: [],
			risk_signals: [
				{
					id: "RISK-202",
					requirement_id: "REQ-202",
					title: "Threshold bypass",
					rationale: "Currency conversion can move a purchase above the configured limit.",
					category: "Authorization",
					severity: "Critical",
				},
			],
			suggested_scenarios: ["Converted-currency threshold"],
			dependencies: ["Role service", "Currency conversion service"],
		},
	];
}

export function useCaseSnapshotFixture(overrides = {}) {
	return {
		snapshot_id: USE_CASE_SNAPSHOT_ID,
		project_id: USE_CASE_PROJECT_ID,
		stage: "use_cases",
		version: 3,
		project_revision: USE_CASE_BASE_REVISION,
		operation: "testcases.generate.use_cases",
		approved: false,
		source_snapshot_id: "snapshot-requirements-v2",
		workflow_run_id: "workflow-run-use-cases-v3",
		source_event_id: "event-use-cases-v3",
		request_id: "request-generate-use-cases-v3",
		actor_user_id: TEST_USER_ID,
		title: "Use cases updated",
		metadata: {
			requirement_analysis_count: 2,
			coverage_plan_count: 2,
			agent_contract_version: "1.0",
		},
		payload: {
			requirement_analysis: useCaseRequirementAnalysisFixture(),
			coverage_plan: useCaseCoveragePlanFixture(),
			review: {
				approved: false,
				score: 72,
				threshold: 85,
				summary: "Use-case planning requires attention before downstream test-case generation.",
				blocking_issues: ["REQ-202 needs an explicit converted-currency scenario."],
				suggestions: ["Add gateway timeout recovery coverage."],
				unmet_criteria: ["REQ-202 needs an explicit converted-currency scenario."],
			},
			coverage_metrics: {
				requirements_total: 2,
				requirements_with_analysis: 2,
				requirements_with_coverage_plan: 2,
				use_case_plan_coverage_ratio: 1,
				planned_scenarios_total: 4,
				happy_path_scenarios_total: 1,
				non_happy_path_scenarios_total: 3,
				must_have_scenarios_total: 3,
				merge_warning_count: 0,
			},
			workflow_settings: { approval_threshold: 85 },
			workflow_diagnostics: { status: "completed", used_fallback: false, warnings: [] },
		},
		created_at: USE_CASE_GENERATED_AT,
		...overrides,
	};
}

export function humanReviewFixture(decision, overrides = {}) {
	return {
		review_id: `review-${decision}`,
		snapshot_id: USE_CASE_SNAPSHOT_ID,
		decision,
		comment: decision === "request_changes" ? "Add converted-currency approval coverage." : "Coverage is ready.",
		reviewer_user_id: TEST_USER_ID,
		reviewer_name: "Playwright E2E",
		reviewer_email: "playwright-e2e@example.com",
		reviewed_at: USE_CASE_DECIDED_AT,
		resulting_project_revision: USE_CASE_BASE_REVISION + 1,
		...overrides,
	};
}

export function useCaseProjectFixture({ reviewState = "pending", snapshot = useCaseSnapshotFixture(), ...overrides } = {}) {
	const latestHumanReview =
		reviewState === "approved"
			? humanReviewFixture("approve", { snapshot_id: snapshot?.snapshot_id })
			: reviewState === "request_changes"
				? humanReviewFixture("request_changes", { snapshot_id: snapshot?.snapshot_id })
				: null;
	const useCasesState = snapshot
		? {
				current_snapshot_id: snapshot.snapshot_id,
				version: snapshot.version,
				approved: reviewState === "approved",
				stale: false,
				stale_reason: null,
				updated_at: latestHumanReview?.reviewed_at || snapshot.created_at,
				operation: snapshot.operation,
				source_snapshot_id: snapshot.source_snapshot_id,
				metadata: {
					coverage_plan_count: snapshot.payload?.coverage_plan?.length || 0,
					...(latestHumanReview ? { latest_human_review: latestHumanReview } : {}),
				},
			}
		: undefined;
	return {
		project_id: USE_CASE_PROJECT_ID,
		name: "Mercury Checkout",
		description: "Use Cases review fixture",
		status: "active",
		owner_user_id: TEST_USER_ID,
		current_revision: latestHumanReview?.resulting_project_revision || USE_CASE_BASE_REVISION,
		created_at: "2026-07-16T09:00:00Z",
		updated_at: latestHumanReview?.reviewed_at || snapshot?.created_at || "2026-07-17T09:00:00Z",
		stage_state: {
			requirements: {
				current_snapshot_id: "snapshot-requirements-v2",
				version: 2,
				approved: true,
				stale: false,
				updated_at: "2026-07-17T09:30:00Z",
				operation: "requirements.refine",
				metadata: {},
			},
			...(useCasesState ? { use_cases: useCasesState } : {}),
		},
		current_snapshots: {
			requirements: {
				snapshot_id: "snapshot-requirements-v2",
				project_id: USE_CASE_PROJECT_ID,
				stage: "requirements",
				version: 2,
				project_revision: 6,
				operation: "requirements.refine",
				approved: true,
				metadata: {},
				payload: {
					requirements: [
						{ id: "REQ-101", text: "Customers can complete express checkout.", review_status: "Approved" },
						{ id: "REQ-202", text: "High-value purchases require supervisor approval.", review_status: "Approved" },
					],
				},
				created_at: "2026-07-17T09:30:00Z",
			},
			...(snapshot ? { use_cases: snapshot } : {}),
		},
		timeline: [],
		execution_runs: [],
		...overrides,
	};
}

export function useCaseScenarioTotal(project) {
	return (project?.current_snapshots?.use_cases?.payload?.coverage_plan || []).reduce(
		(total, group) => total + (Array.isArray(group?.scenarios) ? group.scenarios.length : 0),
		0
	);
}

export function useCaseOrchestratorFixture(project, overrides = {}) {
	const state = project.stage_state?.use_cases || null;
	const latestReview = state?.metadata?.latest_human_review;
	const reviewMatches = latestReview?.snapshot_id && latestReview.snapshot_id === state?.current_snapshot_id;
	const approved = Boolean(reviewMatches && latestReview.decision === "approve" && state?.approved);
	const requestedChanges = Boolean(reviewMatches && latestReview.decision === "request_changes");
	const hasSnapshot = Boolean(project.current_snapshots?.use_cases);
	const useCaseStage = {
		stage: "use_cases",
		status: !hasSnapshot ? "not_started" : approved ? "completed" : "attention_required",
		current_snapshot_id: state?.current_snapshot_id || null,
		version: state?.version || 0,
		approved,
		stale: Boolean(state?.stale),
		stale_reason: state?.stale_reason || null,
		operation: state?.operation || null,
		updated_at: state?.updated_at || null,
		summary: state?.metadata || {},
		blockers: approved
			? []
			: [
					{
						code: "missing_approval",
						message: requestedChanges ? latestReview.comment : "Use Cases must be approved before downstream work.",
						stage: "use_cases",
						action: "approve",
						source_stage: "use_cases",
						severity: "blocking",
					},
				],
	};
	const nextActions = !hasSnapshot
		? [
				{
					action: "generate",
					label: "Generate First Test Suite",
					stage: "test_cases",
					enabled: true,
					primary: true,
					secondary: false,
					reason: "Approved requirements are ready for use-case planning and first-time generation.",
					blockers: [],
				},
			]
		: approved
			? []
			: [
					{
						action: "approve",
						label: "Approve Use Cases",
						stage: "use_cases",
						enabled: true,
						primary: true,
						secondary: false,
						reason: requestedChanges ? `Changes were requested: ${latestReview.comment}` : "Use Cases need human review.",
						blockers: [],
						agent_kind: "use_cases",
						agent_contract_version: "1.0",
						agent_implementation: "local",
					},
				];
	return {
		project_id: project.project_id,
		project_revision: project.current_revision,
		current_stage: !hasSnapshot ? "test_cases" : approved ? "test_cases" : "use_cases",
		stages: {
			requirements: {
				stage: "requirements",
				status: "completed",
				current_snapshot_id: "snapshot-requirements-v2",
				version: 2,
				approved: true,
				stale: false,
				summary: {},
				blockers: [],
			},
			use_cases: useCaseStage,
		},
		next_actions: nextActions,
		blockers: approved ? [] : useCaseStage.blockers,
		has_baseline_test_suite: false,
		upstream_changed: false,
		changed_upstream_stages: [],
		generated_at: "2026-07-17T12:00:00Z",
		...overrides,
	};
}

export function useCaseWorkspaceSummaryFixture(project, overrides = {}) {
	const status = useCaseOrchestratorFixture(project);
	const action = status.next_actions[0] || null;
	const snapshot = project.current_snapshots?.use_cases || null;
	const workspaceProject = workspaceProjectFixture({
		project_id: project.project_id,
		name: project.name,
		project_revision: project.current_revision,
		project_status: project.status,
		current_stage: status.current_stage,
		current_status: status.stages[status.current_stage]?.status || "ready",
		current_snapshot_id: status.stages[status.current_stage]?.current_snapshot_id || null,
		completed_stage_count: status.stages.use_cases?.status === "completed" ? 2 : 1,
		reason: action?.reason || "Use Cases review is complete.",
		updated_at: project.updated_at,
	});
	const workItem = action
		? workspaceWorkItemFixture({
				work_item_id: `work_${project.project_id}_${action.stage}`,
				kind: action.action === "approve" ? "review" : "action",
				project_id: project.project_id,
				project_name: project.name,
				project_revision: project.current_revision,
				stage: action.stage,
				status: status.stages[action.stage]?.status || "ready",
				action: action.action,
				enabled: action.enabled,
				primary: action.primary,
				count: action.stage === "use_cases" ? useCaseScenarioTotal(project) : null,
				reason: action.reason,
				current_snapshot_id: snapshot?.snapshot_id || null,
				updated_at: project.updated_at,
			})
		: null;
	return workspaceSummaryFixture({
		continue_working: workItem,
		projects: [workspaceProject],
		work_items: workItem ? [workItem] : [],
		...overrides,
	});
}

export function applyUseCaseReviewDecision(project, { decision, comment = null } = {}) {
	const next = clone(project);
	const snapshotId = next.current_snapshots.use_cases.snapshot_id;
	const resultingRevision = next.current_revision + 1;
	const latestHumanReview = humanReviewFixture(decision, {
		snapshot_id: snapshotId,
		comment: comment?.trim() || null,
		resulting_project_revision: resultingRevision,
	});
	const state = next.stage_state.use_cases;
	state.approved = decision === "approve";
	state.updated_at = USE_CASE_DECIDED_AT;
	state.metadata = { ...(state.metadata || {}), latest_human_review: latestHumanReview };
	next.current_revision = resultingRevision;
	next.updated_at = USE_CASE_DECIDED_AT;
	return next;
}

export function useCaseReviewResponseFixture(project, { decision, comment = null, requestId = "review-request-1" } = {}) {
	const latestReview = project.stage_state.use_cases.metadata.latest_human_review;
	return {
		review: {
			review_id: latestReview.review_id,
			project_id: project.project_id,
			stage: "use_cases",
			snapshot_id: latestReview.snapshot_id,
			decision,
			comment: comment?.trim() || null,
			reviewer_user_id: TEST_USER_ID,
			reviewer_name: "Playwright E2E",
			reviewer_email: "playwright-e2e@example.com",
			request_id: requestId,
			idempotency_key: `use_cases.review:${requestId}`,
			request_fingerprint: "a".repeat(64),
			timeline_event_id: `timeline-${decision}`,
			base_project_revision: project.current_revision - 1,
			resulting_project_revision: project.current_revision,
			decided_at: USE_CASE_DECIDED_AT,
		},
		project_revision: project.current_revision,
		use_cases_state: project.stage_state.use_cases,
		orchestrator_status: useCaseOrchestratorFixture(project),
	};
}

function projectSummary(project) {
	return {
		project_id: project.project_id,
		name: project.name,
		description: project.description,
		status: project.status,
		owner_user_id: project.owner_user_id,
		current_revision: project.current_revision,
		created_at: project.created_at,
		updated_at: project.updated_at,
		stage_state: project.stage_state,
	};
}

function jsonResponse(route, payload, status = 200) {
	return route.fulfill({
		status,
		contentType: "application/json",
		body: JSON.stringify(payload),
	});
}

function scenarioAt(scenarios, index) {
	if (!scenarios.length) return null;
	return scenarios[Math.min(index, scenarios.length - 1)];
}

export async function installUseCaseReviewApi(page, options = {}) {
	let serverProject = clone(options.initialProject || useCaseProjectFixture());
	const reviewScenarios = options.reviewScenarios || [];
	const detailScenarios = options.detailScenarios || [];
	const statusScenarios = options.statusScenarios || [];
	const summaryScenarios = options.summaryScenarios || [];
	const requests = {
		projectList: [],
		projectDetail: [],
		orchestratorStatus: [],
		orchestratorRuns: [],
		workspaceSummary: [],
		review: [],
	};

	await page.route("**/*", async (route) => {
		const request = route.request();
		if (!["fetch", "xhr"].includes(request.resourceType())) {
			return route.fallback();
		}

		const url = new URL(request.url());
		const { pathname } = url;
		const method = request.method();

		if (pathname === "/auth/me") {
			return jsonResponse(route, {
				sub: TEST_USER_ID,
				email: "playwright-e2e@example.com",
				name: "Playwright E2E",
				picture: null,
			});
		}
		if (pathname === "/reports/usage/me") {
			return jsonResponse(route, { groups: [] });
		}
		if (pathname === "/entitlements/me") {
			return jsonResponse(route, {
				account: { plan_tier: "premium", support_contact_email: "support@example.test" },
				requirements: { remaining: 500, exhausted: false },
				test_cases: { remaining: 500, exhausted: false },
				wallet: { balance_units: 5000, balance_token_display: "5000" },
				shadow_mode: false,
			});
		}
		if (pathname.startsWith("/integrations/")) {
			return jsonResponse(route, { connected: false, connection: null });
		}
		if (pathname === "/workspace/summary" && method === "GET") {
			const index = requests.workspaceSummary.length;
			requests.workspaceSummary.push({ method, url: url.href });
			const scenario = scenarioAt(summaryScenarios, index);
			if (scenario?.gate) await scenario.gate;
			const payload = typeof scenario?.payload === "function" ? scenario.payload(serverProject) : scenario?.payload;
			return jsonResponse(route, payload || useCaseWorkspaceSummaryFixture(serverProject), scenario?.status || 200);
		}
		if (pathname === "/projects" && method === "GET") {
			requests.projectList.push({ method, url: url.href });
			return jsonResponse(route, { projects: [projectSummary(serverProject)] });
		}

		const reviewMatch = pathname.match(/^\/projects\/([^/]+)\/use-cases\/reviews$/);
		if (reviewMatch && method === "POST") {
			const projectId = decodeURIComponent(reviewMatch[1]);
			const payload = request.postDataJSON();
			const headers = await request.allHeaders();
			const index = requests.review.length;
			requests.review.push({ method, url: url.href, projectId, payload, headers });
			const scenario = scenarioAt(reviewScenarios, index);
			if (scenario?.gate) await scenario.gate;
			if (scenario?.serverProject) {
				serverProject = clone(
					typeof scenario.serverProject === "function" ? scenario.serverProject(serverProject, payload) : scenario.serverProject
				);
			}
			if (scenario?.status && scenario.status >= 400) {
				return jsonResponse(route, scenario.payload || { detail: "Use Cases review persistence is unavailable" }, scenario.status);
			}
			serverProject = clone(
				scenario?.nextProject ||
					applyUseCaseReviewDecision(serverProject, {
						decision: payload.decision,
						comment: payload.comment,
					})
			);
			const requestId = headers["x-request-id"] || "review-request-1";
			const responsePayload =
				typeof scenario?.payload === "function"
					? scenario.payload(serverProject, payload, requestId)
					: scenario?.payload || useCaseReviewResponseFixture(serverProject, { ...payload, requestId });
			return jsonResponse(route, responsePayload, scenario?.status || 200);
		}

		const statusMatch = pathname.match(/^\/projects\/([^/]+)\/orchestrator\/status$/);
		if (statusMatch && method === "GET") {
			const index = requests.orchestratorStatus.length;
			const projectId = decodeURIComponent(statusMatch[1]);
			requests.orchestratorStatus.push({ method, url: url.href, projectId });
			const scenario = scenarioAt(statusScenarios, index);
			if (scenario?.gate) await scenario.gate;
			const payload = typeof scenario?.payload === "function" ? scenario.payload(serverProject) : scenario?.payload;
			return jsonResponse(route, payload || useCaseOrchestratorFixture(serverProject), scenario?.status || 200);
		}

		const runsMatch = pathname.match(/^\/projects\/([^/]+)\/orchestrator\/runs$/);
		if (runsMatch && method === "GET") {
			const projectId = decodeURIComponent(runsMatch[1]);
			requests.orchestratorRuns.push({ method, url: url.href, projectId });
			return jsonResponse(route, { runs: [], events: [], checkpoints: [] });
		}

		const detailMatch = pathname.match(/^\/projects\/([^/]+)$/);
		if (detailMatch && method === "GET") {
			const index = requests.projectDetail.length;
			const projectId = decodeURIComponent(detailMatch[1]);
			requests.projectDetail.push({ method, url: url.href, projectId });
			const scenario = scenarioAt(detailScenarios, index);
			if (scenario?.gate) await scenario.gate;
			const payload = typeof scenario?.payload === "function" ? scenario.payload(serverProject) : scenario?.payload;
			return jsonResponse(route, payload || serverProject, scenario?.status || 200);
		}

		return route.fallback();
	});

	return {
		requests,
		getProject: () => clone(serverProject),
		setProject: (project) => {
			serverProject = clone(project);
		},
	};
}
