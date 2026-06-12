import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium, expect } from "@playwright/test";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, "../..");
const outputDir = path.join(repoRoot, "client_submission", "screenshots");
const sampleRequirementsFile = path.join(repoRoot, "sample-requirements.md");
const baseURL = process.argv[2] || process.env.E2E_BASE_URL || "http://127.0.0.1:5173";

const user = {
	sub: "client-demo-user",
	email: "client.demo@example.test",
	name: "Client Demo User",
	picture: null,
};

const requirements = [
	{
		id: "REQ-001",
		text: "The system shall allow portfolio managers to view current holdings and risk exposure by account.",
		source_system: "file",
		source_path: "Synthetic_Investment_Workflow.md",
		source_section: "Portfolio dashboard",
		source_excerpt: "Portfolio managers need holdings, exposure, and exception visibility before trade approval.",
		source_hierarchy: ["Portfolio Management", "Dashboard"],
		review_status: "Needs Review",
		quality_flags: [],
	},
	{
		id: "REQ-002",
		text: "The system shall prevent trade approval when mandate limits or restricted-list controls are breached.",
		source_system: "file",
		source_path: "Synthetic_Investment_Workflow.md",
		source_section: "Trade compliance",
		source_excerpt: "Approval must be blocked when the requested trade breaches mandate rules.",
		source_hierarchy: ["Trading", "Compliance"],
		review_status: "Needs Review",
		quality_flags: ["Compliance"],
	},
];

const groundedContext = {
	artifact_sources: [
		{
			id: "ART-APP-01",
			source_type: "app",
			label: "Portfolio dashboard",
			url: "https://example.com/portfolio",
			status: "Analyzed",
			notes: "Synthetic HTML fixture",
		},
	],
	ui_elements: [
		{
			id: "ART-APP-01-UI-001",
			source_id: "ART-APP-01",
			label: "Portfolio Overview",
			name: "Portfolio Overview",
			element_type: "Page",
			description: "Landing page showing holdings, cash, exposure, and exceptions.",
		},
		{
			id: "ART-APP-01-UI-B-01",
			source_id: "ART-APP-01",
			label: "Approve trade",
			name: "Approve trade",
			element_type: "Button",
			description: "Approval action gated by mandate and restricted-list checks.",
		},
	],
	api_surfaces: [
		{
			id: "ART-APP-01-API-01-GET",
			source_id: "ART-APP-01",
			name: "GET /api/portfolio/{accountId}/exposure",
			description: "Returns current exposure and risk limits.",
			method: "GET",
			path: "/api/portfolio/{accountId}/exposure",
			auth_required: true,
		},
	],
	workflows: [
		{
			id: "WF-001",
			source_id: "ART-APP-01",
			name: "Trade approval workflow",
			description: "Portfolio manager submits a trade, compliance rules are checked, and an approval or rejection is recorded.",
			actors: ["Portfolio Manager", "Compliance Reviewer"],
			states: ["Draft", "Submitted", "Approved", "Blocked"],
			transitions: ["Draft -> Submitted", "Submitted -> Approved", "Submitted -> Blocked"],
		},
	],
	summary: "Registered 1 synthetic artifact source; extracted 2 UI elements, 1 API surface, and 1 workflow.",
};

const coveragePlan = requirements.map((requirement, index) => ({
	requirement_id: requirement.id,
	requirement_text: requirement.text,
	scenarios: [
		{
			id: `${requirement.id}-SC-01`,
			requirement_id: requirement.id,
			scenario_type: "Happy Path",
			title: `Validate ${requirement.id} happy path`,
			objective: "Confirm the core business workflow succeeds.",
			priority: "High",
			must_have: true,
		},
		{
			id: `${requirement.id}-SC-02`,
			requirement_id: requirement.id,
			scenario_type: index === 1 ? "Authorization" : "Negative",
			title: `Validate ${requirement.id} control path`,
			objective: "Confirm control or exception behavior is covered.",
			priority: index === 1 ? "Critical" : "Medium",
			must_have: true,
		},
	],
}));

const requirementAnalysis = [
	{
		requirement_id: "REQ-001",
		requirement_text: requirements[0].text,
		business_rules: [
			{
				id: "REQ-001-BR-01",
				requirement_id: "REQ-001",
				title: "Account holdings are visible",
				description: requirements[0].text,
				rule_type: "Business",
			},
		],
		field_constraints: [],
		role_permissions: [
			{
				id: "REQ-001-RP-01",
				requirement_id: "REQ-001",
				role: "Portfolio Manager",
				action: "View holdings and exposure",
				effect: "Allow",
				conditions: "Authenticated user assigned to account.",
			},
		],
		state_transitions: [],
		risk_signals: [
			{
				id: "REQ-001-RS-01",
				requirement_id: "REQ-001",
				title: "Incorrect exposure displayed",
				rationale: "Could lead to inappropriate trading decisions.",
				category: "Data Integrity",
				severity: "High",
			},
		],
		suggested_scenarios: ["Happy Path", "Negative"],
		dependencies: ["Portfolio data service", "Risk exposure API"],
	},
	{
		requirement_id: "REQ-002",
		requirement_text: requirements[1].text,
		business_rules: [
			{
				id: "REQ-002-BR-01",
				requirement_id: "REQ-002",
				title: "Mandate breaches block approval",
				description: requirements[1].text,
				rule_type: "Authorization",
			},
		],
		field_constraints: [
			{
				id: "REQ-002-FC-01",
				requirement_id: "REQ-002",
				field_name: "requested trade",
				description: "Trade must remain within mandate limits.",
				constraint_type: "Range",
				operator: "<=",
				value: "configured mandate limit",
				negative_example: "Trade exceeds mandate limit.",
			},
		],
		role_permissions: [
			{
				id: "REQ-002-RP-01",
				requirement_id: "REQ-002",
				role: "Portfolio Manager",
				action: "Approve blocked trade",
				effect: "Deny",
				conditions: "Mandate or restricted-list breach exists.",
			},
		],
		state_transitions: [
			{
				id: "REQ-002-ST-01",
				requirement_id: "REQ-002",
				entity: "Trade",
				from_state: "Submitted",
				to_state: "Blocked",
				trigger: "Compliance rule breach",
				guards: "Restricted-list or mandate check fails.",
			},
		],
		risk_signals: [
			{
				id: "REQ-002-RS-01",
				requirement_id: "REQ-002",
				title: "Restricted-list breach",
				rationale: "Regulatory and client mandate impact.",
				category: "Compliance",
				severity: "Critical",
			},
		],
		suggested_scenarios: ["Authorization", "Boundary", "Happy Path"],
		dependencies: ["Compliance rule engine", "Restricted-list feed"],
	},
];

const testCases = [
	{
		id: "TC-001",
		title: "Portfolio manager views holdings and exposure",
		description: "Verifies portfolio managers can open the dashboard and review holdings, exposure, and exception indicators.",
		priority: "High",
		type: "Functional",
		status: "Approved",
		preconditions: "Portfolio manager is assigned to synthetic account ACC-1001 and the exposure API is available.",
		steps: [
			{
				step: 1,
				action: "Sign in as demo.manager@example.test and open Portfolio Overview.",
				expected: "Portfolio Overview loads with the account selector visible.",
				test_data: "ACC-1001",
			},
			{
				step: 2,
				action: "Select ACC-1001 from the account selector.",
				expected: "Holdings, cash, exposure, and exception panels refresh for ACC-1001.",
				test_data: "ACC-1001",
			},
		],
		expected_result: "The assigned portfolio dashboard is visible and source-backed exposure is correct.",
		test_data: "ACC-1001",
		estimated_time: "8 mins",
		automation_status: "To Be Automated",
		component: "Portfolio Dashboard",
		linked_requirement_ids: ["REQ-001"],
		scenario_refs: ["REQ-001-SC-01"],
		source_refs: ["ART-APP-01"],
		tags: ["REQ-001", "scenario:happy-path", "component:portfolio"],
	},
	{
		id: "TC-002",
		title: "Mandate breach blocks trade approval",
		description: "Verifies a trade that breaches mandate limits cannot be approved and records the correct blocked state.",
		priority: "Critical",
		type: "Security",
		status: "Approved",
		preconditions: "Synthetic trade TRD-2002 is submitted and exceeds the configured mandate limit.",
		steps: [
			{
				step: 1,
				action: "Open submitted trade TRD-2002 as a portfolio manager.",
				expected: "Trade details show status Submitted and mandate warning is visible.",
				test_data: "TRD-2002",
			},
			{ step: 2, action: "Click Approve trade.", expected: "Approval is blocked with a mandate breach message.", test_data: null },
			{
				step: 3,
				action: "Review workflow status and audit event.",
				expected: "Trade status changes to Blocked and an audit record is created.",
				test_data: "Audit event type TRADE_APPROVAL_BLOCKED",
			},
		],
		expected_result: "The trade remains blocked and compliance evidence is available.",
		test_data: "TRD-2002",
		estimated_time: "10 mins",
		automation_status: "To Be Automated",
		component: "Trade Compliance",
		linked_requirement_ids: ["REQ-002"],
		scenario_refs: ["REQ-002-SC-02"],
		source_refs: ["ART-APP-01"],
		tags: ["REQ-002", "scenario:authorization", "component:trade-compliance"],
	},
];

function jsonResponse(route, payload, status = 200, headers = {}) {
	const corsHeaders = {
		"access-control-allow-origin": "*",
		"access-control-allow-methods": "GET,POST,OPTIONS",
		"access-control-allow-headers": "authorization,content-type,x-request-id",
	};
	if (route.request().method() === "OPTIONS") {
		return route.fulfill({
			status: 204,
			headers: corsHeaders,
		});
	}

	return route.fulfill({
		status,
		contentType: "application/json",
		headers: { ...corsHeaders, ...headers },
		body: JSON.stringify(payload),
	});
}

async function installRoutes(page) {
	await page.route("**/auth/me", async (route) => jsonResponse(route, user));
	await page.route("**/reports/usage/me", async (route) => jsonResponse(route, { groups: [] }));
	await page.route("**/entitlements/me", async (route) =>
		jsonResponse(route, {
			account: { plan_tier: "premium", support_contact_email: "support@example.test" },
			requirements: { remaining: 245, exhausted: false },
			test_cases: { remaining: 470, exhausted: false },
			wallet: { balance_units: 5000, balance_token_display: "5000" },
			shadow_mode: false,
		})
	);
	await page.route("**/requirements/parse", async (route) =>
		jsonResponse(route, {
			source_name: "Synthetic_Investment_Workflow.md",
			source_names: ["Synthetic_Investment_Workflow.md"],
			raw_text: "Synthetic investment management workflow requirements.",
			requirements,
			approved: true,
			review: {
				approved: true,
				score: 94,
				threshold: 85,
				summary: "Requirements are specific, testable, and ready for generation after business approval.",
				blocking_issues: [],
				suggestions: ["Confirm restricted-list feed ownership during implementation."],
				unmet_criteria: [],
			},
			coverage_metrics: {
				total_requirements: 2,
				unique_requirements: 2,
				duplicate_requirements: 0,
				shall_format_count: 2,
				requirements_per_document: 2,
			},
			workflow_diagnostics: { status: "completed", warnings: [], parser_failures: [], used_fallback: false },
			iteration_history: [
				{
					iteration: 1,
					actor: "RequirementReviewerAgent",
					approved: true,
					score: 94,
					threshold: 85,
					summary: "Approved with one ownership follow-up.",
					artifact_count: 2,
					artifact_ids: ["REQ-001", "REQ-002"],
					blocking_issues: [],
					suggestions: [],
				},
			],
			workflow_settings: { approval_threshold: 85, max_iterations: 3 },
		})
	);
	await page.route("**/requirements/enrich", async (route) => {
		const payload = route.request().postDataJSON();
		return jsonResponse(route, { ...payload, grounded_context: groundedContext });
	});
	await page.route("**/testcases/generate", async (route) =>
		jsonResponse(route, {
			test_cases: testCases,
			approved: true,
			review: {
				approved: true,
				score: 96,
				threshold: 90,
				summary: "Generated test cases meet traceability, scenario coverage, and compliance risk coverage gates.",
				blocking_issues: [],
				suggestions: ["Automate TC-001 and TC-002 first because they are regression-critical."],
				unmet_criteria: [],
			},
			iteration_history: [
				{
					iteration: 1,
					actor: "TestCaseValidatorAgent",
					approved: true,
					score: 96,
					threshold: 90,
					summary: "Coverage gates met.",
					artifact_count: 2,
					artifact_ids: ["TC-001", "TC-002"],
					blocking_issues: [],
					suggestions: [],
				},
			],
			coverage_plan: coveragePlan,
			requirement_analysis: requirementAnalysis,
			coverage_metrics: {
				total_test_cases: 2,
				requirements_total: 2,
				requirements_covered: 2,
				traceability_coverage_ratio: 1,
				scenario_coverage_ratio: 1,
				planned_scenarios_total: 4,
				covered_planned_scenarios: 4,
				must_have_scenario_coverage_ratio: 1,
				grounded_artifact_count: 1,
				source_backed_test_cases: 2,
				grounded_source_backed_case_ratio: 1,
				artifact_reference_coverage_ratio: 1,
				requirements_without_tests: [],
				missing_must_have_scenarios: [],
				high_risk_items_without_tests: [],
			},
			workflow_settings: { approval_threshold: 90, max_iterations: 4, timeout_seconds: 240 },
			workflow_diagnostics: {
				status: "completed",
				warnings: [],
				parser_failures: [],
				used_fallback: false,
				attempt_count: 1,
				best_iteration: 1,
			},
		})
	);
	await page.route("**/automation/execution/preview", async (route) =>
		jsonResponse(route, {
			summary: { executable: 2, manual: 0, unsupported: 0, invalid: 0 },
			executable: testCases.map((testCase) => ({ test_case_id: testCase.id, title: testCase.title })),
			manual: [],
			unsupported: [],
			invalid: [],
		})
	);
	await page.route("**/export/json", async (route) =>
		jsonResponse(route, { export_format: "test_cases_v1", total_count: testCases.length, test_cases: testCases }, 200, {
			"content-disposition": "attachment; filename=test_cases.json",
		})
	);
}

async function screenshot(page, filename) {
	await page.evaluate(() => window.scrollTo(0, 0));
	await page.waitForTimeout(150);
	const pathName = path.join(outputDir, filename);
	await page.screenshot({ path: pathName, fullPage: false });
	return pathName;
}

async function main() {
	await fs.mkdir(outputDir, { recursive: true });
	const browser = await chromium.launch({ headless: true });
	const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, deviceScaleFactor: 1 });

	const publicPage = await context.newPage();
	await publicPage.goto(baseURL);
	const signInButton = publicPage.getByRole("button", { name: /^sign in$/i });
	if (await signInButton.isVisible().catch(() => false)) {
		await signInButton.click();
		await expect(publicPage.getByRole("dialog", { name: /choose a sign-in method/i })).toBeVisible();
	} else {
		await expect(publicPage.getByText(/sign in to parse requirements/i)).toBeVisible({ timeout: 30_000 });
		await expect(publicPage.getByText(/set the vite_firebase_\* variables/i)).toBeVisible();
	}
	await screenshot(publicPage, "01_sign_in_options.png");
	await publicPage.close();

	const page = await context.newPage();
	await installRoutes(page);
	await page.addInitScript(
		({ storageTokenKey, storageUserKey, authToken, authUser }) => {
			window.localStorage.setItem(storageTokenKey, authToken);
			window.localStorage.setItem(storageUserKey, JSON.stringify(authUser));
		},
		{
			storageTokenKey: "tcg.auth.token",
			storageUserKey: "tcg.auth.user",
			authToken: "client-demo-token",
			authUser: user,
		}
	);

	await page.goto(baseURL);
	await expect(page.getByRole("button", { name: /sign out/i })).toBeVisible({ timeout: 30_000 });

	await page.locator('input[type="file"]').setInputFiles(sampleRequirementsFile);
	await page.getByRole("button", { name: /parse requirements/i }).click();
	await expect(page.locator(".requirements-list li")).toHaveCount(2, { timeout: 30_000 });
	await expect(page.getByRole("heading", { name: /requirement workflow diagnostics/i })).toBeVisible();
	await screenshot(page, "02_requirement_review.png");

	await page.getByRole("button", { name: /^Next$/ }).click();
	await page.locator('input[placeholder="https://your-app"]').fill("https://example.com/portfolio");
	await page.locator('input[placeholder="https://prototype"]').fill("https://example.com/prototype");
	await page.locator('input[placeholder="Link1; Link2"]').first().fill("https://example.com/workflow");
	await page.getByRole("button", { name: /analyze context/i }).click();
	await expect(page.getByRole("heading", { name: /grounded context/i })).toBeVisible({ timeout: 30_000 });
	await screenshot(page, "03_grounded_context.png");

	await page.getByRole("button", { name: /^Next$/ }).click();
	await screenshot(page, "04_template_setup.png");
	await page.getByRole("button", { name: /^Next$/ }).click();
	await page.getByRole("button", { name: /generate test cases/i }).click();
	await expect(page.locator(".test-cases-table tbody tr").or(page.locator(".case-card")).first()).toBeVisible({ timeout: 30_000 });
	await screenshot(page, "05_generated_test_cases.png");

	const requirementAnalysisPanel = page.locator(".collapsible-panel-summary", { hasText: /requirement analysis/i });
	await expect(requirementAnalysisPanel).toBeVisible();
	await requirementAnalysisPanel.click();
	await screenshot(page, "06_requirement_analysis.png");

	await expect(page.getByRole("heading", { name: /test-case workflow diagnostics/i })).toBeVisible();
	await screenshot(page, "07_workflow_diagnostics.png");

	await page.getByRole("button", { name: /^Next$/ }).click();
	await expect(page.getByRole("heading", { name: /^automation$/i })).toBeVisible();
	await page.getByRole("button", { name: /^Next$/ }).click();
	await expect(page.getByRole("heading", { name: /export test cases/i })).toBeVisible();
	await expect(page.getByRole("button", { name: /json/i }).first()).toBeEnabled();
	await screenshot(page, "08_export_ready.png");

	await browser.close();
	console.log(
		JSON.stringify(
			{
				outputDir,
				files: [
					"01_sign_in_options.png",
					"02_requirement_review.png",
					"03_grounded_context.png",
					"04_template_setup.png",
					"05_generated_test_cases.png",
					"06_requirement_analysis.png",
					"07_workflow_diagnostics.png",
					"08_export_ready.png",
				],
			},
			null,
			2
		)
	);
}

main().catch(async (error) => {
	console.error(error);
	process.exit(1);
});
