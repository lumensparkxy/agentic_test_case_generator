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
export function groupImprovementFindings(review, assessment, findings) {
	// The server's combined review copies these exact assessment messages into blocking issues.
	const assessmentMessages = new Set(
		[
			assessment?.reason,
			...(assessment?.obligations || []).filter((item) => item.status !== "covered").map((item) => `${item.obligation}: ${item.reason}`),
			...(assessment?.case_grounding || []).flatMap((item) =>
				(item.prerequisites || [])
					.filter((gap) => gap.required && !gap.execution_prerequisite_name)
					.map((gap) => `${gap.status}: ${gap.description}. ${gap.reason}`)
			),
		].map((message) => message?.trim())
	);
	const blocking = new Set((review?.blocking_issues || []).map((item) => String(item).trim()));
	return [
		{ label: "Structural blockers", rows: findings.filter((item) => blocking.has(item) && !assessmentMessages.has(item)) },
		{ label: "Other review criteria", rows: findings.filter((item) => !blocking.has(item) && !assessmentMessages.has(item)) },
		{ label: "Assessment findings", rows: findings.filter((item) => assessmentMessages.has(item)), recordedAssessment: true },
	].filter(({ rows }) => rows.length);
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
