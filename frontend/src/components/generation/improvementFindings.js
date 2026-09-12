export function improvementFindings(review) {
	return [
		...new Set([...(review?.blocking_issues || []), ...(review?.unmet_criteria || [])].map((item) => String(item).trim()).filter(Boolean)),
	];
}
export function findingCaseIds(finding, testCases) {
	return testCases
		.filter(({ id }) => id && new RegExp(`(?<![\\w-])${id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![\\w-])`).test(finding))
		.map(({ id }) => id);
}
export function improvementFeedback(findings, instructions) {
	return [
		"Address the requested fixes below. Preserve unrelated test cases and their IDs where possible.",
		findings.length ? `Selected findings:\n${findings.map((finding) => `- ${finding}`).join("\n")}` : "",
		instructions.trim() ? `Additional instructions:\n${instructions.trim()}` : "",
	]
		.filter(Boolean)
		.join("\n\n");
}
