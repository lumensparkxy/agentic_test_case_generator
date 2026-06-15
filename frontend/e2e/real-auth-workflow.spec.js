import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import {
	AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT,
	STORAGE_AUTH_TOKEN,
	STORAGE_AUTH_USER,
	buildTestAccessToken,
	buildTestUser,
	sampleRequirementsFile,
} from "./support/auth.js";
import { edgeProfileDir, expect, test } from "./support/edgePersistent.js";

const allowedPriorities = new Set(["Critical", "High", "Medium", "Low"]);
const allowedTypes = new Set(["Functional", "Integration", "E2E", "Regression", "Smoke", "Security", "Performance", "Usability", "UAT"]);
const minimumStructuredCaseRatio = 0.8;
const qaProjectNamePrefix = "E2E Edge Main Flow";

function responsePath(response) {
	const url = new URL(response.url());
	return `${url.pathname}${url.search}`;
}

function escapeRegExp(value) {
	return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function buildQaProjectName() {
	return `${qaProjectNamePrefix} ${new Date().toISOString().replace(/[:.]/g, "-")}`;
}

function projectStage(page, label) {
	return page.locator(".project-stage-pill", { hasText: label });
}

async function expectProjectStage(page, label, expectedText) {
	await expect(projectStage(page, label)).toContainText(expectedText, { timeout: 60_000 });
}

async function expectProjectRevision(page, projectName, revisionPattern) {
	await expect(page.locator(".project-workspace-header")).toContainText(
		new RegExp(`${escapeRegExp(projectName)} · revision ${revisionPattern}`),
		{ timeout: 60_000 }
	);
}

async function readExecutionCount(executionResults, label) {
	const text = (await executionResults.locator(".workflow-diagnostics-pill", { hasText: new RegExp(`^${label}\\b`) }).textContent()) || "";
	return Number.parseInt(text.match(/\d+/)?.[0] || "0", 10);
}

async function expectAutomationPreviewReady(page, testInfo) {
	await page.getByPlaceholder("staging, dev, customer-a").fill("local-e2e");
	const previewButton = page.getByRole("button", { name: /^Preview Execution$/ });
	await expect(previewButton).toBeEnabled({ timeout: 30_000 });
	await previewButton.click();

	const runButton = page.getByRole("button", { name: /^Run [1-9]\d* Candidates?$/ });
	await expect(page.locator(".workflow-diagnostics-pill", { hasText: /^Executable\b/ })).toContainText(/Executable\s+[1-9]\d*/, {
		timeout: 60_000,
	});
	await expect(runButton).toBeEnabled({ timeout: 30_000 });

	const runButtonText = (await runButton.textContent()) || "";
	const executableCount = Number.parseInt(runButtonText.match(/Run\s+(\d+)/)?.[1] || "0", 10);
	expect(executableCount, `Expected at least one automation candidate, saw button text "${runButtonText}".`).toBeGreaterThan(0);
	testInfo.annotations.push({ type: "automation-executable-count", description: `${executableCount}` });

	await expectProjectStage(page, "Execution", /v1/);

	if (process.env.E2E_RUN_AUTOMATION !== "1") {
		testInfo.annotations.push({ type: "automation-run-mode", description: "preview-only" });
		return { executableCount, executionStatus: "preview-only", passedCount: 0, failedCount: 0, invalidCount: 0 };
	}

	testInfo.annotations.push({ type: "automation-run-mode", description: "run" });
	await runButton.click();

	const executionResults = page.locator(".result-section", { hasText: "Execution Results" }).last();
	await expect(executionResults.getByRole("heading", { name: /^Execution Results$/ })).toBeVisible({ timeout: 360_000 });
	const executionStatus = ((await executionResults.locator(".review-banner").textContent()) || "").trim().toLowerCase();
	expect(executionStatus).toMatch(/^(passed|failed)$/);

	const passedCount = await readExecutionCount(executionResults, "Passed");
	const failedCount = await readExecutionCount(executionResults, "Failed");
	const invalidCount = await readExecutionCount(executionResults, "Invalid");
	expect(passedCount + failedCount + invalidCount).toBe(executableCount);

	await expect(executionResults).toContainText(/Artifacts root:/);
	await expect(executionResults.locator("tbody tr")).toHaveCount(executableCount);
	await expect(
		executionResults
			.locator("code")
			.filter({ hasText: /generated\/playwright/ })
			.first()
	).toBeVisible();
	await expect(
		executionResults
			.locator("code")
			.filter({ hasText: /artifacts\/playwright/ })
			.first()
	).toBeVisible();

	await expectProjectStage(page, "Execution", /v2/);
	const executionHistory = page.locator(".project-history-block", { hasText: "Execution Runs" });
	await expect(executionHistory).toContainText(/local-e2e/i, { timeout: 60_000 });
	await expect(executionHistory).toContainText(new RegExp(executionStatus, "i"));
	await expect(executionHistory).toContainText(new RegExp(`${passedCount}\\s+passed\\s+/\\s+${failedCount}\\s+failed`));

	return { executableCount, executionStatus, passedCount, failedCount, invalidCount };
}

async function readAuthState(page) {
	if (await page.getByRole("button", { name: /sign out/i }).isVisible()) {
		return "authenticated";
	}
	if (await page.getByText(/set the vite_firebase_\* variables/i).isVisible()) {
		return "config-missing";
	}
	if (await page.getByRole("button", { name: /^sign in$/i }).isVisible()) {
		return "unauthenticated";
	}
	if (await page.getByText(/checking session/i).isVisible()) {
		return "checking";
	}
	return "unknown";
}

function canUseLocalJwtFallback() {
	return process.env.AUTH_TOKEN_MODE === AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT && Boolean(process.env.JWT_SECRET_KEY);
}

async function seedLocalJwtFallbackSession(page, testInfo) {
	const user = buildTestUser({
		name: "Playwright Real Auth Fallback",
	});
	const token = buildTestAccessToken(user, { requireBackendCompatibility: true });

	await page.evaluate(
		({ storageTokenKey, storageUserKey, authToken, authUser }) => {
			window.localStorage.setItem(storageTokenKey, authToken);
			window.localStorage.setItem(storageUserKey, JSON.stringify(authUser));
		},
		{
			storageTokenKey: STORAGE_AUTH_TOKEN,
			storageUserKey: STORAGE_AUTH_USER,
			authToken: token,
			authUser: user,
		}
	);
	await page.reload({ waitUntil: "domcontentloaded" });

	await expect
		.poll(
			async () => {
				return readAuthState(page);
			},
			{
				timeout: 60_000,
				message: "Expected local backend JWT fallback session to authenticate the app.",
			}
		)
		.toBe("authenticated");

	testInfo.annotations.push({ type: "auth-session", description: "backend-jwt-fallback" });
	return { savedSessionAvailable: false, humanLoginRequired: false, backendJwtFallback: true };
}

async function ensureAuthenticatedSession(page, testInfo) {
	await page.goto("/");

	let authState = "unknown";
	await expect
		.poll(
			async () => {
				authState = await readAuthState(page);
				return authState;
			},
			{
				timeout: 60_000,
				message: "Expected the application auth check to settle before running the main workflow.",
			}
		)
		.toMatch(/^(authenticated|unauthenticated|config-missing)$/);

	if (authState === "authenticated") {
		testInfo.annotations.push({ type: "auth-session", description: "saved-session" });
		return { savedSessionAvailable: true, humanLoginRequired: false, backendJwtFallback: false };
	}

	if (authState === "config-missing") {
		throw new Error("environment issue: Firebase frontend configuration is missing, so real provider sign-in cannot start.");
	}

	if (canUseLocalJwtFallback()) {
		return seedLocalJwtFallbackSession(page, testInfo);
	}

	if (process.env.E2E_INTERACTIVE_LOGIN !== "1") {
		throw new Error(
			`missing session: no authenticated Edge session exists at ${edgeProfileDir}. Run "npm run test:e2e:edge-login", complete login in the manually launched Edge window, close it, then rerun this spec. To use the non-interactive local fallback, set AUTH_TOKEN_MODE=${AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT} and JWT_SECRET_KEY in the repo .env.`
		);
	}

	throw new Error(
		`missing session: Google/Firebase sign-in must be bootstrapped in manually launched Edge, not Playwright-controlled Edge. Run "npm run test:e2e:edge-login", complete login for ${edgeProfileDir}, close Edge, then rerun this spec.`
	);
}

async function createQaProject(page, testInfo) {
	await expect(page.getByRole("button", { name: /sign out/i })).toBeVisible({ timeout: 30_000 });
	await expect(page.getByRole("heading", { name: /^QA Project$/ })).toBeVisible();

	const projectName = buildQaProjectName();
	const projectNameInput = page.getByPlaceholder("New QA project name");
	await expect(projectNameInput).toBeEnabled({ timeout: 30_000 });
	await projectNameInput.fill(projectName);
	await page.getByRole("button", { name: /^New Project$/ }).click();

	await expectProjectRevision(page, projectName, "1");
	await expectProjectStage(page, "Requirements", /Not started/);
	await expectProjectStage(page, "Context", /Not started/);
	await expectProjectStage(page, "Test Cases", /Not started/);
	await expectProjectStage(page, "Reports", /Not started/);
	testInfo.annotations.push({ type: "qa-project", description: projectName });
	return projectName;
}

async function openGenerateTab(page, projectName) {
	await page.locator('input[type="file"]').setInputFiles(sampleRequirementsFile);
	await page.getByRole("button", { name: /parse requirements/i }).click();

	await expect
		.poll(async () => page.locator(".requirement-review-table tbody tr").count(), {
			timeout: 120_000,
			message: "Expected parsed requirements to appear after uploading the sample file.",
		})
		.toBeGreaterThan(0);
	await expectProjectStage(page, "Requirements", /v1/);
	await expectProjectRevision(page, projectName, "[2-9]\\d*");
	await page.getByRole("button", { name: /approve non-rejected/i }).click();

	await expect(page.getByText(/approved for test generation/i)).toBeVisible();

	await page.getByRole("button", { name: /^Next$/ }).click();
	await page.locator('input[placeholder="https://your-app"]').fill("https://example.com/app");
	await page.getByRole("button", { name: /analyze context/i }).click();

	await expect(page.getByRole("heading", { name: /grounded context/i })).toBeVisible({ timeout: 120_000 });
	await expect
		.poll(async () => page.locator(".artifact-source-item").count(), {
			timeout: 30_000,
			message: "Expected analyzed context artifacts to appear in the Context tab.",
		})
		.toBeGreaterThan(0);
	await expectProjectStage(page, "Context", /v1/);
	await expectProjectRevision(page, projectName, "[3-9]\\d*");

	await page.getByRole("button", { name: /^Next$/ }).click();
	await page.getByRole("button", { name: /^Next$/ }).click();
}

test.describe.configure({ mode: "serial", retries: 0 });

test.describe("Real-auth Microsoft Edge main workflow", () => {
	test("saved Edge session can parse, generate, and export high-quality test cases", async ({ page, context }, testInfo) => {
		const pageErrors = [];
		const consoleErrors = [];
		const failedApiResponses = [];
		const requestFailures = [];

		page.on("pageerror", (error) => pageErrors.push(error.message));
		page.on("console", (message) => {
			if (message.type() === "error") {
				const location = message.location();
				const sourceUrl = location.url || "";
				let sourcePath;
				try {
					sourcePath = new URL(sourceUrl || "http://local.invalid/favicon.ico").pathname;
				} catch {
					sourcePath = sourceUrl;
				}
				const isBenignMissingFavicon =
					/Failed to load resource: the server responded with a status of 404/i.test(message.text()) &&
					/\/favicon\.(ico|png|svg)$/.test(sourcePath);
				if (!isBenignMissingFavicon) {
					consoleErrors.push(sourceUrl ? `${message.text()} (${sourceUrl})` : message.text());
				}
			}
		});
		page.on("requestfailed", (request) => {
			requestFailures.push(`${request.method()} ${request.url()} ${request.failure()?.errorText || ""}`.trim());
		});
		page.on("response", (response) => {
			if (response.status() >= 400 && /127\.0\.0\.1:8000|localhost:8000/.test(response.url())) {
				failedApiResponses.push(`${response.status()} ${response.request().method()} ${responsePath(response)}`);
			}
		});

		const authResult = await ensureAuthenticatedSession(page, testInfo);
		const projectName = await createQaProject(page, testInfo);
		await openGenerateTab(page, projectName);

		await page.getByRole("button", { name: /generate from \d+ approved/i }).click();
		const generatedTestCasesTab = page.getByRole("tab", { name: /generated test cases/i });
		await expect(generatedTestCasesTab).toBeVisible({ timeout: 360_000 });
		await generatedTestCasesTab.click();

		await expect
			.poll(
				async () => {
					const tableRows = await page.locator(".test-cases-table tbody tr").count();
					const cards = await page.locator(".case-card").count();
					return tableRows + cards;
				},
				{
					timeout: 360_000,
					message: "Expected generated test cases to appear in the UI.",
				}
			)
			.toBeGreaterThan(0);
		await expectProjectStage(page, "Use Cases", /v1/);
		await expectProjectStage(page, "Test Cases", /v1/);
		await expectProjectRevision(page, projectName, "[5-9]\\d*");

		await page.getByRole("tab", { name: /requirement analysis/i }).click();
		await expect(page.locator(".collapsible-panel-title", { hasText: /requirement analysis/i })).toBeVisible();
		await page.getByRole("tab", { name: /diagnostics/i }).click();
		await expect(page.getByRole("heading", { name: /test-case workflow diagnostics/i })).toBeVisible();
		await generatedTestCasesTab.click();

		await page.getByRole("button", { name: /^Next$/ }).click();
		await expect(page.getByRole("heading", { name: /^Automation$/i })).toBeVisible();
		const executionSummary = await expectAutomationPreviewReady(page, testInfo);
		await expectProjectRevision(page, projectName, "[6-9]\\d*");
		await page.getByRole("button", { name: /^Next$/ }).click();

		const draftExportToggle = page.getByLabel(/export draft anyway/i);
		if (await draftExportToggle.isVisible().catch(() => false)) {
			await draftExportToggle.check();
			await page
				.getByLabel(/reason for exporting this draft/i)
				.fill("E2E quality validation export after reviewing generated draft output.");
		}

		const jsonButton = page.getByRole("button", { name: /json/i }).first();
		await expect(jsonButton).toBeEnabled({ timeout: 30_000 });
		const download = await Promise.all([page.waitForEvent("download"), jsonButton.click()]).then(([item]) => item);
		await expectProjectStage(page, "Reports", /v[1-9]\d*/);
		await expect(page.locator(".project-history-block", { hasText: "Latest Report" })).toContainText(/json/i, { timeout: 60_000 });

		const downloadPath = path.join(os.tmpdir(), `tcg-real-auth-e2e-${Date.now()}.json`);
		await download.saveAs(downloadPath);
		const exported = JSON.parse(await fs.readFile(downloadPath, "utf8"));
		const testCases = exported.test_cases || [];

		const quality = {
			total: testCases.length,
			withDescriptions: testCases.filter((tc) => tc.description?.trim()).length,
			withExpectedResults: testCases.filter((tc) => tc.expected_result?.trim()).length,
			withRequirementTags: testCases.filter((tc) => Array.isArray(tc.tags) && tc.tags.some((tag) => /^REQ-\d+/i.test(tag))).length,
			withTwoOrMoreSteps: testCases.filter((tc) => Array.isArray(tc.steps) && tc.steps.length >= 2).length,
			charFragmentCases: testCases
				.filter((tc) => {
					if (!Array.isArray(tc.steps) || tc.steps.length <= 10) {
						return false;
					}
					const tinyActions = tc.steps.filter((step) => (step?.action?.trim()?.length || 0) <= 2).length;
					return tinyActions >= Math.ceil(tc.steps.length * 0.4);
				})
				.map((tc) => tc.id),
			invalidPriorities: testCases.filter((tc) => !allowedPriorities.has(tc.priority)).map((tc) => tc.id),
			invalidTypes: testCases.filter((tc) => !allowedTypes.has(tc.type)).map((tc) => tc.id),
			untitledCases: testCases.filter((tc) => !tc.title?.trim() || /untitled/i.test(tc.title)).map((tc) => tc.id),
		};

		testInfo.annotations.push(
			{ type: "browser-used", description: context.__edgeExecutionDetails.browserUsed },
			{ type: "browser-channel", description: context.__edgeExecutionDetails.browserChannel },
			{ type: "profile-dir", description: context.__edgeExecutionDetails.profileDir },
			{ type: "profile-mode", description: context.__edgeExecutionDetails.profileMode },
			{ type: "auth-saved-session", description: `${authResult.savedSessionAvailable}` },
			{ type: "auth-human-assisted", description: `${authResult.humanLoginRequired}` },
			{ type: "auth-backend-jwt-fallback", description: `${authResult.backendJwtFallback}` },
			{ type: "automation-run-candidates", description: `${executionSummary.executableCount}` },
			{ type: "automation-run-status", description: executionSummary.executionStatus },
			{ type: "quality-summary", description: JSON.stringify(quality) }
		);
		console.log("Generated test case quality summary:", quality);
		console.log("Real-auth Edge execution details:", {
			browser: context.__edgeExecutionDetails.browserUsed,
			channel: context.__edgeExecutionDetails.browserChannel,
			headless: context.__edgeExecutionDetails.headless,
			profileDir: context.__edgeExecutionDetails.profileDir,
			profileMode: context.__edgeExecutionDetails.profileMode,
			savedSessionAvailable: authResult.savedSessionAvailable,
			humanLoginRequired: authResult.humanLoginRequired,
			backendJwtFallback: authResult.backendJwtFallback,
			automationRunCandidates: executionSummary.executableCount,
			automationRunStatus: executionSummary.executionStatus,
		});

		expect(quality.total).toBeGreaterThan(0);
		expect(quality.withDescriptions).toBe(quality.total);
		expect(quality.withExpectedResults).toBe(quality.total);
		expect(quality.withRequirementTags).toBe(quality.total);
		expect(quality.withTwoOrMoreSteps).toBeGreaterThanOrEqual(Math.ceil(quality.total * minimumStructuredCaseRatio));
		expect(quality.charFragmentCases).toEqual([]);
		expect(quality.invalidPriorities).toEqual([]);
		expect(quality.invalidTypes).toEqual([]);
		expect(quality.untitledCases).toEqual([]);
		expect(pageErrors).toEqual([]);
		expect(failedApiResponses).toEqual([]);
		expect(requestFailures).toEqual([]);
		expect(consoleErrors).toEqual([]);
	});
});
