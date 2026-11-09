import { buildProjectPath, PROJECT_DESTINATIONS } from "./workflowRoutes";

export function buildSavedCasePath(projectId, snapshotId, caseId) {
	const query = new URLSearchParams({ suite: snapshotId, case: caseId });
	return `${buildProjectPath(projectId, PROJECT_DESTINATIONS.TEST_CASES)}?${query}`;
}

export function readSavedCaseLink(search) {
	const query = new URLSearchParams(search);
	if (!query.has("suite") && !query.has("case")) return null;
	const suites = query.getAll("suite");
	const cases = query.getAll("case");
	if (suites.length !== 1 || cases.length !== 1 || !suites[0] || !cases[0]) return { snapshotId: "", caseId: "" };
	return { snapshotId: suites[0], caseId: cases[0] };
}

function caseContent(testCase) {
	// Saved objects can omit nulls and return fields in a different order.
	return JSON.stringify(testCase, (_, value) =>
		value && typeof value === "object" && !Array.isArray(value)
			? Object.fromEntries(
					Object.keys(value)
						.sort()
						.filter((key) => value[key] !== null)
						.map((key) => [key, value[key]])
				)
			: value
	);
}

export function savedCasePathForSelection(projectId, suite, testCase) {
	if (!projectId || !suite?.snapshot_id || !testCase?.id || !Array.isArray(suite.payload?.test_cases)) return "";
	const matches = suite.payload.test_cases.filter((item) => item.id === testCase.id);
	if (matches.length !== 1 || (matches[0] !== testCase && caseContent(matches[0]) !== caseContent(testCase))) return "";
	return buildSavedCasePath(projectId, suite.snapshot_id, testCase.id);
}
