export function requiresIndependentReview(assessment) {
	return (
		assessment?.status === "unknown" ||
		assessment?.model_status === "unknown" ||
		Number(assessment?.diagnostics?.unverified_group_count) > 0
	);
}

export function improvementFindings(review, assessment) {
	const setupReason =
		assessment?.design_status === "assessed_complete" && assessment.execution_status === "blocked" ? assessment.reason : null;
	const pendingReviewReason = requiresIndependentReview(assessment) ? assessment?.reason?.trim() : null;
	return [
		...new Set(
			[...(review?.blocking_issues || []), ...(review?.unmet_criteria || [])]
				.map((item) => String(item).trim())
				.filter((item) => item && item !== setupReason && item !== pendingReviewReason)
		),
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
