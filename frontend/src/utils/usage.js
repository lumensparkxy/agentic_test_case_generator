export const normalizeUsageMetric = (value) => {
	const parsed = Number.parseInt(`${value ?? 0}`, 10);
	return Number.isFinite(parsed) ? parsed : 0;
};

export const formatPlanLabel = (planTier) => {
	const normalized = `${planTier || "pilot"}`.trim().toLowerCase();
	if (!normalized) {
		return "Pilot";
	}
	return normalized.charAt(0).toUpperCase() + normalized.slice(1);
};

export const buildEmptyUsageSummary = (user) => ({
	scopeType: "individual",
	scopeKey: user?.sub ? `user:${user.sub}` : "user:current",
	displayName: user?.name || user?.email || user?.sub || "Current user",
	totalEvents: 0,
	requirementsGeneratedCount: 0,
	requirementsModifiedCount: 0,
	testCasesGeneratedCount: 0,
	testCasesModifiedCount: 0,
	hasData: false,
});

export const buildUsageSummaryFromSource = (source, user, group, hasData = true) => ({
	scopeType: group?.scope_type || "individual",
	scopeKey: group?.scope_key || (user?.sub ? `user:${user.sub}` : "user:current"),
	displayName: source?.name || source?.email || group?.display_name || user?.name || user?.email || user?.sub || "Current user",
	totalEvents: normalizeUsageMetric(source?.total_events),
	requirementsGeneratedCount: normalizeUsageMetric(source?.requirements_generated_count),
	requirementsModifiedCount: normalizeUsageMetric(source?.requirements_modified_count),
	testCasesGeneratedCount: normalizeUsageMetric(source?.test_cases_generated_count),
	testCasesModifiedCount: normalizeUsageMetric(source?.test_cases_modified_count),
	hasData,
});

export const getCurrentUserUsageSummary = (report, user) => {
	if (!user) {
		return null;
	}

	const fallback = buildEmptyUsageSummary(user);
	const subject = `${user.sub || ""}`.trim();
	const email = `${user.email || ""}`.trim().toLowerCase();
	const groups = Array.isArray(report?.groups) ? report.groups : [];
	const matchesUser = (candidate) => {
		const candidateUserId = `${candidate?.user_id || ""}`.trim();
		const candidateEmail = `${candidate?.email || ""}`.trim().toLowerCase();
		return (subject && candidateUserId === subject) || (email && candidateEmail === email);
	};

	for (const group of groups) {
		const users = Array.isArray(group?.users) ? group.users : [];
		const matchedUser = users.find(matchesUser);
		if (matchedUser) {
			return buildUsageSummaryFromSource(matchedUser, user, group, true);
		}

		if (group?.scope_type === "individual") {
			const scopeKey = `${group?.scope_key || ""}`.trim();
			const displayName = `${group?.display_name || ""}`.trim().toLowerCase();
			if ((subject && scopeKey === `user:${subject}`) || (email && displayName === email)) {
				return buildUsageSummaryFromSource(group, user, group, true);
			}
		}
	}

	return fallback;
};
