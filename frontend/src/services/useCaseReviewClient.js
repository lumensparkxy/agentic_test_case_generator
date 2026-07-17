import { API_CONTRACT_ENDPOINTS } from "../api/generated/api-contracts";

const REVIEW_PATH_TEMPLATE = API_CONTRACT_ENDPOINTS.projectUseCasesReview.path;

const buildReviewPath = (projectId) => REVIEW_PATH_TEMPLATE.replace("{project_id}", encodeURIComponent(`${projectId || ""}`.trim()));

const parseResponsePayload = async (response) => {
	const text = await response.text();
	if (!text) {
		return null;
	}
	try {
		return JSON.parse(text);
	} catch {
		return { detail: text };
	}
};

const getResponseMessage = (payload, fallback) => {
	if (typeof payload?.detail === "string") {
		return payload.detail;
	}
	if (typeof payload?.detail?.message === "string") {
		return payload.detail.message;
	}
	if (Array.isArray(payload?.detail) && typeof payload.detail[0]?.msg === "string") {
		return payload.detail[0].msg;
	}
	if (typeof payload?.message === "string") {
		return payload.message;
	}
	return fallback;
};

export class UseCaseReviewRequestError extends Error {
	constructor(message, { status = 0, conflict = null } = {}) {
		super(message);
		this.name = "UseCaseReviewRequestError";
		this.status = status;
		this.conflict = conflict;
	}
}

export async function submitUseCaseReviewDecision(
	request,
	{ projectId, snapshotId, baseProjectRevision, decision, comment = "", requestId, signal } = {}
) {
	if (typeof request !== "function") {
		throw new TypeError("An authenticated request function is required to submit a Use Cases review.");
	}
	if (!`${projectId || ""}`.trim() || !`${snapshotId || ""}`.trim()) {
		throw new TypeError("The current project and Use Cases snapshot are required for review.");
	}
	if (!Number.isInteger(baseProjectRevision) || baseProjectRevision < 0) {
		throw new TypeError("The current project revision is required for review.");
	}
	if (!new Set(["approve", "request_changes"]).has(decision)) {
		throw new TypeError("Choose Approve or Request changes before submitting.");
	}

	const normalizedComment = `${comment || ""}`.trim();
	if (decision === "request_changes" && !normalizedComment) {
		throw new TypeError("Add a comment describing the requested changes.");
	}

	const response = await request(buildReviewPath(projectId), {
		method: "POST",
		signal,
		headers: {
			"Content-Type": "application/json",
			"X-Request-ID": requestId,
		},
		body: JSON.stringify({
			snapshot_id: `${snapshotId}`.trim(),
			base_project_revision: baseProjectRevision,
			decision,
			comment: normalizedComment || null,
		}),
	});
	const payload = await parseResponsePayload(response);
	if (!response.ok) {
		const conflictDetail = response.status === 409 && payload?.detail && typeof payload.detail === "object" ? payload.detail : null;
		throw new UseCaseReviewRequestError(getResponseMessage(payload, "The Use Cases decision could not be saved."), {
			status: response.status,
			conflict: conflictDetail,
		});
	}

	return payload;
}
