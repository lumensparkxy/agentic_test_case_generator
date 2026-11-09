import { findingCaseIds, improvementFindings } from "./improvementFindings";

const PRIORITY_ORDER = ["Critical", "High", "Medium", "Low"];
const MAX_PRIORITY_CASES = 6;
const savedRows = (suite) => (Array.isArray(suite?.payload?.test_cases) ? suite.payload.test_cases : []);
const isActiveCase = (item) =>
	typeof item?.id === "string" && item.id.trim() && typeof item.title === "string" && item.title.trim() && item.status !== "Deprecated";

export function findActiveSavedCase(suite, caseId) {
	if (!caseId) return null;
	const matches = savedRows(suite).filter((item) => item?.id === caseId);
	return matches.length === 1 && isActiveCase(matches[0]) ? matches[0] : null;
}

export function prioritySavedCases(suite) {
	const rows = savedRows(suite);
	const counts = new Map();
	for (const row of rows) counts.set(row?.id, (counts.get(row?.id) || 0) + 1);
	const candidates = rows.filter((row) => isActiveCase(row) && counts.get(row.id) === 1);
	const findings = improvementFindings(suite?.payload?.review, suite?.payload?.substantive_assessment);
	const needsReview = new Set(findings.flatMap((finding) => findingCaseIds(finding, candidates)));
	const priority = (item) => PRIORITY_ORDER.indexOf(PRIORITY_ORDER.includes(item.priority) ? item.priority : "Medium");
	return candidates
		.sort((left, right) => Number(needsReview.has(right.id)) - Number(needsReview.has(left.id)) || priority(left) - priority(right))
		.slice(0, MAX_PRIORITY_CASES);
}
