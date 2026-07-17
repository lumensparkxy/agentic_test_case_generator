const PREVIEW_BUCKETS = Object.freeze(["executable", "manual", "unsupported", "invalid"]);
const NORMALIZATION_VERSION = 1;
const CONSISTENCY_MESSAGE =
	"Preview data was inconsistent and was normalized to the available candidates. Preview execution again before running.";

const isRecord = (value) => Boolean(value && typeof value === "object" && !Array.isArray(value));
const hasOwn = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
const cleanId = (value) => (typeof value === "string" ? value.trim() : "");

const hasNormalizedShape = (value) =>
	isRecord(value) &&
	value.normalizationVersion === NORMALIZATION_VERSION &&
	PREVIEW_BUCKETS.every((bucket) => Array.isArray(value[bucket])) &&
	isRecord(value.summary) &&
	typeof value.isConsistent === "boolean" &&
	typeof value.requiresRefresh === "boolean";

const addIssue = (issues, issue) => {
	issues.add(issue);
};

const normalizeCandidate = (candidate, bucket, issues) => {
	if (!isRecord(candidate)) {
		addIssue(issues, `${bucket}:invalid_candidate`);
		return null;
	}

	const id = cleanId(candidate.id);
	const sourceTestCaseId = cleanId(candidate.source_test_case_id);
	if (!id || !sourceTestCaseId || typeof candidate.title !== "string") {
		addIssue(issues, `${bucket}:invalid_candidate`);
		return null;
	}
	if (candidate.status !== bucket) {
		addIssue(issues, `${bucket}:status_mismatch`);
		return null;
	}

	return {
		...candidate,
		id,
		source_test_case_id: sourceTestCaseId,
	};
};

const readCandidateEntries = (candidateCollection, issues) => {
	const entries = [];
	for (const bucket of PREVIEW_BUCKETS) {
		const rawBucket = candidateCollection?.[bucket];
		if (rawBucket === undefined) {
			addIssue(issues, `${bucket}:missing`);
			continue;
		}
		if (!Array.isArray(rawBucket)) {
			addIssue(issues, `${bucket}:not_array`);
			continue;
		}

		rawBucket.forEach((candidate) => {
			const normalizedCandidate = normalizeCandidate(candidate, bucket, issues);
			if (normalizedCandidate) {
				entries.push({ bucket, candidate: normalizedCandidate });
			}
		});
	}
	return entries;
};

const removeCandidateCollisions = (entries, issues) => {
	const uniqueEntries = [];
	const exactKeys = new Set();

	for (const entry of entries) {
		const { bucket, candidate } = entry;
		const exactKey = `${bucket}\u0000${candidate.id}\u0000${candidate.source_test_case_id}`;
		if (exactKeys.has(exactKey)) {
			addIssue(issues, `${bucket}:duplicate_candidate`);
			continue;
		}
		exactKeys.add(exactKey);
		uniqueEntries.push(entry);
	}

	const entriesByCandidateId = new Map();
	for (const entry of uniqueEntries) {
		const candidateIdEntries = entriesByCandidateId.get(entry.candidate.id) || [];
		candidateIdEntries.push(entry);
		entriesByCandidateId.set(entry.candidate.id, candidateIdEntries);
	}

	const collidingCandidateIds = new Set();
	for (const [candidateId, matchingEntries] of entriesByCandidateId) {
		if (matchingEntries.length > 1) {
			collidingCandidateIds.add(candidateId);
			addIssue(issues, "candidate_id_collision");
		}
	}

	return uniqueEntries.filter((entry) => !collidingCandidateIds.has(entry.candidate.id));
};

const buildBuckets = (entries) => {
	const buckets = Object.fromEntries(PREVIEW_BUCKETS.map((bucket) => [bucket, []]));
	for (const entry of entries) {
		buckets[entry.bucket].push(entry.candidate);
	}
	return buckets;
};

const compareCounts = (rawCounts, label, buckets, issues, { required = false } = {}) => {
	if (rawCounts === undefined) {
		if (required) {
			addIssue(issues, `${label}:missing`);
		}
		return;
	}
	if (!isRecord(rawCounts)) {
		addIssue(issues, `${label}:not_object`);
		return;
	}

	for (const bucket of PREVIEW_BUCKETS) {
		if (!hasOwn(rawCounts, bucket)) {
			addIssue(issues, `${label}:${bucket}:missing`);
			continue;
		}
		const count = rawCounts[bucket];
		if (!Number.isInteger(count) || count < 0) {
			addIssue(issues, `${label}:${bucket}:invalid`);
			continue;
		}
		if (count !== buckets[bucket].length) {
			addIssue(issues, `${label}:${bucket}:mismatch`);
		}
	}
};

const normalizeWarnings = (warnings, issues) => {
	if (warnings === undefined) {
		return [];
	}
	if (!Array.isArray(warnings)) {
		addIssue(issues, "warnings:not_array");
		return [];
	}
	const normalized = warnings.filter((warning) => typeof warning === "string");
	if (normalized.length !== warnings.length) {
		addIssue(issues, "warnings:invalid_entry");
	}
	return normalized;
};

/**
 * Normalizes both live execution-preview responses and persisted execution
 * snapshot payloads. Persisted previews remain visible but require a fresh
 * preview before their candidates are safe to execute.
 */
export function normalizeAutomationPreview(rawPreview, { source: requestedSource } = {}) {
	if (hasNormalizedShape(rawPreview)) {
		return rawPreview;
	}

	const hasPreview = rawPreview !== null && rawPreview !== undefined;
	const outer = isRecord(rawPreview) ? rawPreview : null;
	const wrappedPayload = isRecord(outer?.payload) ? outer.payload : null;
	const payload = wrappedPayload || outer || {};
	const hasTopLevelBuckets = Boolean(payload && PREVIEW_BUCKETS.some((bucket) => hasOwn(payload, bucket)));
	const hasPersistedCandidates = isRecord(payload?.candidates);
	const source =
		requestedSource === "live" || requestedSource === "persisted"
			? requestedSource
			: wrappedPayload || hasPersistedCandidates
				? "persisted"
				: "live";
	const issues = new Set();

	if (!hasPreview) {
		return {
			executable: [],
			manual: [],
			unsupported: [],
			invalid: [],
			warnings: [],
			summary: { executable: 0, manual: 0, unsupported: 0, invalid: 0 },
			hasPreview: false,
			isConsistent: true,
			consistencyMessage: null,
			consistencyIssues: [],
			source,
			isLegacy: false,
			requiresRefresh: false,
			normalizationVersion: NORMALIZATION_VERSION,
		};
	}

	if (hasTopLevelBuckets && hasPersistedCandidates) {
		addIssue(issues, "candidate_collections:ambiguous");
	}
	const candidateCollection = hasTopLevelBuckets ? payload : hasPersistedCandidates ? payload.candidates : null;
	const isLegacy = !candidateCollection;
	if (!candidateCollection) {
		addIssue(issues, "candidate_collections:missing");
	}

	const entries = removeCandidateCollisions(readCandidateEntries(candidateCollection, issues), issues);
	const buckets = buildBuckets(entries);
	const summary = Object.fromEntries(PREVIEW_BUCKETS.map((bucket) => [bucket, buckets[bucket].length]));
	compareCounts(payload.summary, "summary", buckets, issues, { required: true });
	compareCounts(payload.candidate_counts, "candidate_counts", buckets, issues);
	const warnings = normalizeWarnings(payload.warnings, issues);
	const consistencyIssues = [...issues];
	const isConsistent = consistencyIssues.length === 0;

	return {
		...payload,
		...buckets,
		warnings,
		summary,
		hasPreview: true,
		isConsistent,
		consistencyMessage: isConsistent ? null : CONSISTENCY_MESSAGE,
		consistencyIssues,
		source,
		isLegacy,
		requiresRefresh: source === "persisted" || isLegacy || !isConsistent,
		normalizationVersion: NORMALIZATION_VERSION,
	};
}

const normalizeSelectedIds = (selectedIds) => {
	const values = Array.isArray(selectedIds) ? selectedIds : selectedIds instanceof Set ? [...selectedIds] : [];
	return new Set(values.map(cleanId).filter(Boolean));
};

const getRunnablePreview = (preview) => {
	const normalized = hasNormalizedShape(preview) ? preview : normalizeAutomationPreview(preview);
	return normalized.hasPreview && normalized.isConsistent && !normalized.requiresRefresh ? normalized : null;
};

export function getDefaultSelectedCandidateIds(preview) {
	const normalized = getRunnablePreview(preview);
	return normalized ? normalized.executable.map((candidate) => candidate.id) : [];
}

export function resolveSelectedExecutableCandidates(preview, selectedIds) {
	const normalized = getRunnablePreview(preview);
	if (!normalized) {
		return [];
	}
	const selected = normalizeSelectedIds(selectedIds);
	if (!selected.size) {
		return [];
	}
	return normalized.executable.filter((candidate) => selected.has(candidate.id));
}

export function intersectSelectedCandidateIds(preview, selectedIds) {
	return resolveSelectedExecutableCandidates(preview, selectedIds).map((candidate) => candidate.id);
}

export function resolveSelectedExecutableSourceIds(preview, selectedIds) {
	return resolveSelectedExecutableCandidates(preview, selectedIds).map((candidate) => candidate.source_test_case_id);
}

export { PREVIEW_BUCKETS };
