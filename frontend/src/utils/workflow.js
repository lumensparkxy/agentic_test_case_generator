export const buildWorkflowSettingsPayload = (settings) => {
	const payload = Object.entries(settings || {}).reduce((acc, [key, value]) => {
		const normalized = `${value ?? ""}`.trim();
		if (!normalized) {
			return acc;
		}
		const parsed = Number.parseInt(normalized, 10);
		if (Number.isFinite(parsed)) {
			acc[key] = parsed;
		}
		return acc;
	}, {});

	return Object.keys(payload).length ? payload : null;
};

export const getReviewScoreMeta = (review) => {
	const parsedScore = Number.parseInt(`${review?.score ?? 0}`, 10);
	const parsedThreshold = Number.parseInt(`${review?.threshold ?? 0}`, 10);
	const score = Number.isFinite(parsedScore) ? Math.max(0, Math.min(100, parsedScore)) : 0;
	const threshold = Number.isFinite(parsedThreshold) ? Math.max(0, parsedThreshold) : 0;

	return {
		score,
		threshold,
		scoreLabel: `Quality score ${score}/100`,
		thresholdLabel: threshold > 0 ? `Approval threshold ${threshold}` : null,
	};
};
