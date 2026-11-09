import { useEffect, useMemo, useRef, useState } from "react";
import { API_CONTRACT_ENDPOINTS } from "../api/generated/api-contracts";
import { createRequestId, parseApiError } from "../services/apiClient";
import useReviewDraft from "./useReviewDraft";

const MAX_DIRECT_UPDATES = 6;
const CHECK_ERROR = "Could not check application status. Check status again.";

function scopedSource(project) {
	const snapshots = project?.current_snapshots;
	const states = project?.stage_state;
	const plan = snapshots?.use_cases;
	const planState = states?.use_cases;
	const review = planState?.metadata?.latest_human_review;
	const suite = snapshots?.test_cases;
	return Boolean(
		plan?.metadata?.use_case_refresh?.previous_snapshot_id &&
		plan.operation === "use_cases.generate" &&
		plan.metadata.scenario_refresh_predecessor?.source_test_cases_snapshot_id === suite?.snapshot_id &&
		plan.metadata.use_case_refresh.previous_snapshot_id === suite?.metadata?.source_use_case_snapshot_id &&
		plan.snapshot_id === planState?.current_snapshot_id &&
		!planState.stale &&
		planState.approved &&
		review?.snapshot_id === plan.snapshot_id &&
		review.decision === "approve" &&
		suite?.snapshot_id &&
		suite.snapshot_id === states?.test_cases?.current_snapshot_id &&
		states.test_cases.stale
	);
}

function matchingResult(result, pointer) {
	if (!pointer || !result || result.request_id !== pointer.requestId) return false;
	const ids = result.accepted_recommendation_ids;
	const cases = result.changed_test_case_ids;
	const expected = pointer.body.accepted_recommendation_ids;
	return (
		result.analysis_snapshot_id === pointer.body.analysis_snapshot_id &&
		result.source_snapshot_id === pointer.sourceId &&
		result.result_snapshot_id &&
		result.result_snapshot_id !== pointer.sourceId &&
		Number.isInteger(result.project_revision) &&
		result.project_revision > pointer.body.base_project_revision &&
		Array.isArray(ids) &&
		ids.length === expected.length &&
		new Set(ids).size === ids.length &&
		ids.every((id) => expected.includes(id)) &&
		Array.isArray(cases) &&
		cases.length === expected.length &&
		new Set(cases).size === cases.length &&
		result.updated_count === cases.length &&
		Number.isInteger(result.preserved_count) &&
		result.preserved_count >= 0
	);
}

export default function useScopedImpactApplication({
	project,
	identity,
	scopedEnabled,
	scopedRequest,
	captureScope,
	isCurrent,
	receiveProject,
}) {
	const projectId = project?.project_id;
	const revision = project?.current_revision;
	const analysis = project?.current_snapshots?.impact_analysis;
	const analysisId = analysis?.snapshot_id;
	const analysisState = project?.stage_state?.impact_analysis;
	const suite = project?.current_snapshots?.test_cases;
	const sourceId = suite?.snapshot_id;
	const analysisCurrent =
		analysisId && analysisId === analysisState?.current_snapshot_id && !analysisState.stale && analysis.source_snapshot_id === sourceId;
	const candidate = scopedEnabled && scopedSource(project);
	const scope = scopedEnabled ? JSON.stringify([identity, projectId]) : "";
	const owner = useMemo(() => ({ scope, read: null, action: null, submission: null }), [scope]);
	const activeOwner = useRef(owner);
	activeOwner.current = owner;
	const callbacks = useRef(null);
	callbacks.current = { scopedRequest, captureScope, isCurrent, receiveProject, revision, analysisId };
	const recovery = useReviewDraft({
		identity: scopedEnabled ? identity : "",
		projectId,
		snapshotId: "scoped-impact-v1",
		name: "scoped-impact-application",
		initialValue: null,
	});
	const saved = recovery.value;
	const pointer =
		saved?.requestId &&
		saved.sourceId &&
		saved.body?.analysis_snapshot_id &&
		Number.isInteger(saved.body.base_project_revision) &&
		Array.isArray(saved.body.accepted_recommendation_ids)
			? saved
			: null;
	const [readState, setRead] = useState(null);
	const [actionState, setAction] = useState(null);
	const [submissionState, setSubmission] = useState(null);
	const read = readState?.owner === owner ? readState : null;
	const action = actionState?.owner === owner ? actionState : null;
	const submitting = submissionState?.owner === owner;
	const data = read?.data;
	const statusFresh = Boolean(
		read && !read.error && read.revision === revision && read.analysisId === analysisId && read.requestId === pointer?.requestId
	);
	const result = matchingResult(data?.result, pointer) ? data.result : null;
	const attempt = data?.attempt || null;
	const pending = Boolean(pointer && !result);
	const active = Boolean(submitting || pending || attempt);
	const enrolled = Boolean(scope && (active || result || (candidate && !(statusFresh && data?.enabled === false))));
	const eligibleIds =
		statusFresh && data?.enabled && data.eligible && analysisCurrent ? new Set(data.selectable_recommendation_ids || []) : new Set();
	const selectable = new Map(
		(eligibleIds.size ? analysis?.payload?.recommendations || [] : [])
			.filter(
				(item) =>
					eligibleIds.has(item.recommendation_id) && item.action === "update" && item.impact_source === "direct" && item.test_case_id
			)
			.map((item) => [item.recommendation_id, item.test_case_id])
	);
	const validPartialSelection = (ids) =>
		Array.isArray(ids) &&
		ids.length > 0 &&
		ids.length <= MAX_DIRECT_UPDATES &&
		ids.every((id) => selectable.has(id)) &&
		new Set(ids.map((id) => selectable.get(id))).size === ids.length;
	const validSelection = (ids) => ids.length === eligibleIds.size && validPartialSelection(ids);
	const canStart = Boolean(
		enrolled &&
		candidate &&
		analysisCurrent &&
		data?.enabled &&
		data?.eligible &&
		statusFresh &&
		!data.blocked &&
		(!data.result || result) &&
		!attempt &&
		!action?.busy &&
		!submitting
	);
	const canRetry = Boolean(
		canStart &&
		pending &&
		!pointer.releaseRequested &&
		pointer.sourceId === sourceId &&
		pointer.body.analysis_snapshot_id === analysisId &&
		pointer.body.base_project_revision === revision &&
		validSelection(pointer.body.accepted_recommendation_ids)
	);
	const suiteIsCurrent =
		suite?.snapshot_id &&
		suite.snapshot_id === project?.stage_state?.test_cases?.current_snapshot_id &&
		!project.stage_state.test_cases.stale;
	const savedRows = suite?.payload?.test_cases || [];
	const resultCurrent = Boolean(
		result &&
		suiteIsCurrent &&
		result.changed_test_case_ids.every((id) => savedRows.filter((row) => row.id === id).length === 1) &&
		(sourceId === result.result_snapshot_id ||
			(suite.operation === "test_cases.review" && suite.source_snapshot_id === result.result_snapshot_id))
	);
	const operation =
		resultCurrent && result.analysis_snapshot_id === analysisId
			? { ...result, status: "applied", added_count: 0, deprecated_count: 0 }
			: null;
	const boundSelection =
		operation?.accepted_recommendation_ids ||
		(attempt && attempt.analysis_snapshot_id === analysisId ? attempt.accepted_recommendation_ids : null) ||
		(pending && pointer.body.analysis_snapshot_id === analysisId ? pointer.body.accepted_recommendation_ids : null);
	const pathFor = (endpoint) => endpoint.path.replace("{project_id}", encodeURIComponent(projectId));

	const checkStatus = async (requested = pointer, { released = null, failure = "" } = {}) => {
		const api = callbacks.current;
		const projectScope = api.captureScope();
		if (!scope || activeOwner.current !== owner || projectScope?.projectId !== projectId || projectScope.userId !== identity) return;
		owner.read?.abort();
		const controller = new AbortController();
		owner.read = controller;
		const current = () => !controller.signal.aborted && activeOwner.current === owner && api.isCurrent(projectScope);
		const requestId = requested?.requestId;
		try {
			const endpoint = API_CONTRACT_ENDPOINTS.projectImpactUpdateStatus;
			const path = pathFor(endpoint);
			const query = requestId ? `?${new URLSearchParams({ request_id: requestId })}` : "";
			const response = await api.scopedRequest(`${path}${query}`, { method: endpoint.method, signal: controller.signal });
			if (!response.ok) throw new Error(CHECK_ERROR);
			const next = await response.json();
			if (!current()) return;
			setRead({ owner, data: next, revision: api.revision, analysisId: api.analysisId, requestId });
			const receipt = matchingResult(next.result, requested) ? next.result : null;
			let error =
				released && next.attempt?.operation_id === released.operation_id
					? "The release was not confirmed. Check the current application."
					: receipt
						? ""
						: next.result
							? "The saved application does not match this request. Check the current analysis."
							: failure;
			if (receipt && receipt.project_revision > api.revision) {
				const projectResponse = await api.scopedRequest(`/projects/${encodeURIComponent(projectId)}`, {
					method: "GET",
					signal: controller.signal,
				});
				const updated = projectResponse.ok ? await projectResponse.json() : null;
				if (!current()) return;
				if (updated?.project_id === projectId && updated.current_revision >= receipt.project_revision)
					api.receiveProject(updated, projectScope);
				else error = "The application is saved. Check application status again to load the suite.";
			} else if (requested && !next.result && !next.attempt && !next.blocked && (failure || requested.releaseRequested)) {
				if (owner.submission?.pointer.requestId === requestId) {
					owner.submission = null;
					setSubmission(null);
				}
				recovery.clear();
			}
			if (owner.action || failure || error) setAction({ owner, error });
		} catch {
			if (current()) {
				setRead((prior) => ({ owner, data: prior?.owner === owner ? prior.data : null, error: CHECK_ERROR }));
				if (owner.action) setAction({ owner });
			}
		} finally {
			if (owner.read === controller) owner.read = null;
		}
	};

	useEffect(() => {
		if (!scope || !(candidate || analysisId || pointer || attempt) || owner.action || owner.submission) return;
		void checkStatus();
		const controller = owner.read;
		return () => controller?.abort();
	}, [owner, scope, candidate, analysisId, pointer?.requestId, revision]);

	const check = async (release = null) => {
		if (!scope || owner.action || (release && (!statusFresh || attempt !== release))) return;
		const action = { done: null };
		owner.action = action;
		owner.read?.abort();
		setAction({ owner, busy: true });
		const requested = release && pointer?.requestId === release.request_id ? { ...pointer, releaseRequested: true } : pointer;
		if (requested !== pointer) recovery.setValue(requested);
		action.done = (async () => {
			try {
				if (release) {
					const endpoint = API_CONTRACT_ENDPOINTS.projectImpactUpdateRelease;
					const path = pathFor(endpoint).replace("{operation_id}", encodeURIComponent(release.operation_id));
					await callbacks.current.scopedRequest(path, {
						method: endpoint.method,
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({ fence: release.fence }),
					});
				}
			} catch {
				// A status read confirms the exact persisted outcome of the release.
			}
			await checkStatus(requested, { released: release });
		})();
		try {
			await action.done;
		} finally {
			if (owner.action === action) owner.action = null;
		}
	};

	const apply = async (ids, retry = false) => {
		if (!canStart || owner.action || owner.submission || !validSelection(ids) || (retry ? !canRetry : pending)) return;
		const requested = retry
			? pointer
			: {
					requestId: createRequestId(),
					sourceId,
					body: { analysis_snapshot_id: analysisId, accepted_recommendation_ids: ids, base_project_revision: revision },
				};
		const submission = { owner, pointer: requested };
		owner.submission = submission;
		owner.read?.abort();
		recovery.setValue(requested);
		setAction(null);
		setSubmission(submission);
		let failure = "";
		try {
			const endpoint = API_CONTRACT_ENDPOINTS.projectImpactUpdateApply;
			const response = await callbacks.current.scopedRequest(pathFor(endpoint), {
				method: endpoint.method,
				headers: { "Content-Type": "application/json", "X-Request-ID": requested.requestId },
				body: JSON.stringify(requested.body),
			});
			if (!response.ok && response.status < 500) failure = await parseApiError(response, "The selected recommendations were declined.");
		} catch {
			// A lost response is resolved by the exact request ID, without another POST.
		} finally {
			await owner.action?.done;
			if (activeOwner.current === owner && owner.submission === submission) {
				await checkStatus(requested, { failure });
				if (owner.submission === submission) owner.submission = null;
				setSubmission((prior) => (prior === submission ? null : prior));
			}
		}
	};

	return {
		enrolled,
		providerRestricted: data?.enabled === true && !enrolled,
		analysisCurrent,
		selectable,
		requiredCount: eligibleIds.size || (operation?.accepted_recommendation_ids.length ?? 0),
		validPartialSelection,
		validSelection,
		limit: MAX_DIRECT_UPDATES,
		operation,
		result,
		resultCurrent,
		boundSelection,
		active,
		pending,
		attempt,
		submitting,
		checking: action?.busy === true,
		statusFresh,
		blocked: data?.blocked === true,
		canApply: canStart && !pending,
		canRetry,
		error: read?.error || action?.error || recovery.error,
		apply,
		check: () => check(),
		release: () => attempt && check(attempt),
		retry: () => canRetry && apply(pointer.body.accepted_recommendation_ids, true),
		dismiss:
			pending &&
			statusFresh &&
			!data?.result &&
			!data?.blocked &&
			!attempt &&
			(pointer.sourceId !== sourceId ||
				pointer.body.analysis_snapshot_id !== analysisId ||
				pointer.body.base_project_revision !== revision ||
				pointer.releaseRequested ||
				data?.enabled === false)
				? recovery.clear
				: null,
	};
}
