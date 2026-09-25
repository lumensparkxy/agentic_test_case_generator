import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createHash, randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import { expect } from "@playwright/test";
import { assertLocalJwtCompatibilityMode, seedAuthenticatedSession } from "./auth.js";

const root = fileURLToPath(new URL("../../../", import.meta.url));
export const roomSource = path.join(root, "backend/tests/fixtures/room-booking-requirements.md");
export const roomContext = path.join(root, "frontend/e2e/fixtures/room-booking-context.txt");
const expectedSourceIds = ["ROOM-101", "ROOM-205", "ROOM-310", "ROOM-440"];

export async function liveFixture(page, testInfo) {
	assertLocalJwtCompatibilityMode();
	const apiBase = process.env.E2E_API_BASE || "http://127.0.0.1:8000";
	expect(["127.0.0.1", "localhost", "[::1]"]).toContain(new URL(apiBase).hostname);
	const { token, user } = await seedAuthenticatedSession(page);
	const apiRecords = [];
	const api = async (method, url, data, requestId = randomUUID()) => {
		const response = await page.request.fetch(`${apiBase}${url}`, {
			method,
			data,
			headers: { Authorization: `Bearer ${token}`, "X-Request-ID": requestId },
			timeout: 180_000,
		});
		if (method !== "GET") {
			apiRecords.push({
				path: url,
				method,
				request_id: requestId,
				status: response.status(),
				input: data,
				output: response.status() === 204 ? null : await response.json().catch(() => null),
			});
		}
		return response;
	};
	const checked = async (method, url, data) => {
		const response = await api(method, url, data);
		expect(response.ok(), `${method} ${url}: ${response.status()} ${(await response.text()).slice(0, 1200)}`).toBe(true);
		return response.json();
	};
	await checked("GET", "/auth/me");
	const name = `Live Room Booking ${randomUUID()}`;
	const project = await checked("POST", "/projects", {
		name,
		description: "Test-owned synthetic live integration fixture; automated reviewer actions are test setup, not human business acceptance.",
	});
	expect(project.owner_user_id).toBe(user.sub);
	const prefix = `/projects/${project.project_id}`;
	const evidence = {
		project_id: project.project_id,
		project_name: name,
		started_at: new Date().toISOString(),
		api_base: apiBase,
		records: apiRecords,
		blocking_gates: [],
	};
	const evidenceFiles = [
		"backend/app/adk_client.py",
		"backend/app/agents/use_case_agent.py",
		"backend/app/agents/test_case_agent.py",
		"backend/app/agents/scenario_assessment.py",
		"backend/app/agents/substantive_review.py",
		"backend/tests/fixtures/room-booking-requirements.md",
		"frontend/e2e/fixtures/room-booking-context.txt",
	];
	evidence.checkout = {
		head: execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim(),
		files: Object.fromEntries(
			await Promise.all(
				evidenceFiles.map(async (filename) => [
					filename,
					createHash("sha256")
						.update(await fs.readFile(path.join(root, filename)))
						.digest("hex"),
				])
			)
		),
	};
	evidence.runtime_note =
		"Run the backend from this checkout. Correlate captured request IDs with backend logs for actual model/provider and usage; browser evidence alone does not attest server configuration or per-run cost.";
	const captures = [];
	const listener = (response) => {
		const url = new URL(response.url());
		if (url.origin !== new URL(apiBase).origin || !/^\/(requirements|testcases|export|projects)\b/.test(url.pathname)) return;
		if (response.request().method() === "GET") return;
		captures.push(
			(async () => {
				const record = {
					path: url.pathname,
					method: response.request().method(),
					status: response.status(),
					request_id: response.headers()["x-request-id"] || null,
					at: new Date().toISOString(),
				};
				try {
					record.input = response.request().postDataJSON();
				} catch {
					record.input = "Multipart source upload; fixture file retained separately.";
				}
				try {
					record.output = await response.json();
				} catch {
					record.output = "Non-JSON or empty response";
				}
				evidence.records.push(record);
			})()
		);
	};
	page.on("response", listener);
	return {
		api,
		checked,
		prefix,
		project,
		evidence,
		read: () => checked("GET", prefix),
		async save(label, value) {
			const filename = testInfo.outputPath(`${label}.json`);
			await fs.writeFile(filename, JSON.stringify(value, null, 2));
			await testInfo.attach(label, { path: filename, contentType: "application/json" });
		},
		async close() {
			page.off("response", listener);
			await Promise.allSettled(captures);
			try {
				const latest = await checked("GET", prefix);
				evidence.final_project = latest;
				expect(latest.project_id).toBe(project.project_id);
				expect(latest.owner_user_id).toBe(user.sub);
				expect(latest.name).toBe(name);
				const archived = await checked("PATCH", prefix, { status: "archived", base_project_revision: latest.current_revision });
				expect(archived.status).toBe("archived");
				evidence.cleanup = "Exact test-owned project archived";
			} finally {
				evidence.finished_at = new Date().toISOString();
				await fs.writeFile(testInfo.outputPath("live-evidence.json"), JSON.stringify(evidence, null, 2));
				await testInfo.attach("live-evidence", { path: testInfo.outputPath("live-evidence.json"), contentType: "application/json" });
			}
		},
	};
}

export async function uiResponse(page, pathEnd, action, { timeout = 180_000, status = 200 } = {}) {
	const waiting = page.waitForResponse((r) => new URL(r.url()).pathname.endsWith(pathEnd) && r.request().method() !== "GET", { timeout });
	await action();
	const response = await waiting;
	const detail = response.status() === status ? "" : await response.text().catch(() => "Response body unavailable");
	expect(response.status(), `${pathEnd}: ${detail.slice(0, 1000)}`).toBe(status);
	return response;
}

export function assertSourceMapping(requirements) {
	const actual = new Set(requirements.flatMap((r) => (r.sources || []).flatMap((s) => s.original_requirement_ids || [])));
	for (const id of expectedSourceIds) expect(actual.has(id), `Missing original source ID ${id}`).toBe(true);
	expect(
		requirements.every((r) => (r.sources || []).some((s) => s.excerpt_verified && s.excerpt.trim())),
		"Every imported requirement needs verified source evidence"
	).toBe(true);
}

export async function importSource(page, fixture, source = roomSource) {
	await page.goto(`${fixture.prefix}/requirements`);
	await expect(page.getByRole("heading", { name: "Requirement Review Workbench" })).toBeVisible();
	await page.locator('input[type="file"]').setInputFiles(source);
	const response = await uiResponse(page, "/requirements/parse", () =>
		page.getByRole("button", { name: "Parse Requirements", exact: true }).click()
	);
	const preview = await response.json();
	expect(preview.project_id).toBe(fixture.project.project_id);
	await expect(page.getByRole("heading", { name: "Compare incoming requirements" })).toBeVisible();
	return preview;
}

export async function applyImport(page, fixture, preview) {
	const response = await uiResponse(page, `/requirement-imports/${preview.import_id}/apply`, () =>
		page.getByRole("button", { name: "Apply reviewed changes", exact: true }).click()
	);
	return response.json();
}

export async function approveRequirements(page, fixture) {
	const project = await fixture.read();
	if (project.current_snapshots.requirements.payload.requirements.some((r) => r.review_status !== "Approved")) {
		await uiResponse(page, "/requirements/reviews", () => page.getByRole("button", { name: "Approve non-rejected", exact: true }).click());
	}
	const latest = await fixture.read();
	expect(latest.stage_state.requirements.approved).toBe(true);
	return latest;
}

export async function approveScenarios(page, fixture) {
	const current = await fixture.read();
	const snapshot = current.current_snapshots.use_cases;
	const assessment = snapshot.payload.semantic_assessment || snapshot.payload.review?.semantic_assessment;
	// A live check must not manufacture human approval over failed/unknown design quality.
	expect(assessment?.status, `Use Cases design gate blocked: ${JSON.stringify(assessment)}`).toBe("passed");
	expect(snapshot.payload.review?.approved, "Use Cases structural/machine review failed").toBe(true);
	await page.goto(`${fixture.prefix}/use-cases`);
	await page.getByRole("region", { name: "Use Cases review actions" }).getByRole("button", { name: "Approve all", exact: true }).click();
	const decision = page.getByRole("form", { name: "Human review decision" });
	await decision
		.getByRole("textbox", { name: /Review comment/ })
		.fill(
			"Automated synthetic fixture reviewer: source and current design gates checked for this integration run. This is not real human business acceptance."
		);
	await uiResponse(page, "/use-cases/reviews", () => decision.getByRole("button", { name: "Approve Use Cases", exact: true }).click());
	const latest = await fixture.read();
	expect(latest.stage_state.use_cases.approved).toBe(true);
	return latest;
}

export function caseQuality(testCases) {
	const allowedPriorities = new Set(["Critical", "High", "Medium", "Low"]);
	const allowedTypes = new Set(["Functional", "Integration", "E2E", "Regression", "Smoke", "Security", "Performance", "Usability", "UAT"]);
	return {
		total: testCases.length,
		withDescriptions: testCases.filter((c) => c.description?.trim()).length,
		withExpectedResults: testCases.filter((c) => c.expected_result?.trim()).length,
		withRequirementLinks: testCases.filter((c) => c.linked_requirement_ids?.length).length,
		withTwoOrMoreSteps: testCases.filter((c) => Array.isArray(c.steps) && c.steps.length >= 2).length,
		charFragmentCases: testCases
			.filter(
				(c) => c.steps?.length > 10 && c.steps.filter((s) => (s.action?.trim()?.length || 0) <= 2).length >= Math.ceil(c.steps.length * 0.4)
			)
			.map((c) => c.id),
		invalidPriorities: testCases.filter((c) => !allowedPriorities.has(c.priority)).map((c) => c.id),
		invalidTypes: testCases.filter((c) => !allowedTypes.has(c.type)).map((c) => c.id),
		untitledCases: testCases.filter((c) => !c.title?.trim() || /untitled/i.test(c.title)).map((c) => c.id),
	};
}
