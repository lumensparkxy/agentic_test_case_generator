import { useEffect, useMemo, useRef, useState } from "react";
import { API_CONTRACT_ENDPOINTS } from "../api/generated/api-contracts";
import { createRequestId } from "../services/apiClient";
import useReviewDraft from "./useReviewDraft";

function sameGroups(left, right) {
	return (
		Array.isArray(left) &&
		Array.isArray(right) &&
		left.length === right.length &&
		new Set(left).size === left.length &&
		left.every((id) => right.includes(id))
	);
}

function matchingResult(result, operation) {
	return Boolean(
		operation &&
		result?.request_id === operation.requestId &&
		result.previous_snapshot_id === operation.predecessorId &&
		result.snapshot_id &&
		result.snapshot_id !== operation.predecessorId &&
		Number.isInteger(result.project_revision) &&
		result.project_revision > operation.body.base_project_revision &&
		Array.isArray(result.retained_requirement_ids) &&
		Array.isArray(result.removed_requirement_ids) &&
		sameGroups(result.refreshed_requirement_ids, operation.body.refresh_requirement_ids)
	);
}

const statusError = "Could not check scenario refresh status. Check status again.";

export default function useScopedScenarioRefresh({ enabled, identity, project, predecessorId, request, readProject }) {
	const projectId = project?.project_id;
	const revision = project?.current_revision;
	const scope = enabled ? JSON.stringify([identity, projectId]) : "";
	const owner = useMemo(() => ({ scope, read: null, action: null, submission: null }), [scope]);
	const activeOwner = useRef(owner);
	activeOwner.current = owner;
	const activeCallbacks = useRef(null);
	activeCallbacks.current = { request, readProject, revision };
	const recovery = useReviewDraft({
		identity: enabled ? identity : "",
		projectId,
		snapshotId: "scenario-refresh-v1",
		name: "scenario-refresh",
		initialValue: null,
	});
	const saved = recovery.value;
	const operation =
		saved?.requestId &&
		saved.predecessorId &&
		Number.isInteger(saved.body?.base_project_revision) &&
		Array.isArray(saved.body?.refresh_requirement_ids)
			? saved
			: null;
	const [statusState, setStatus] = useState(null);
	const [actionState, setAction] = useState(null);
	const [submissionState, setSubmission] = useState(null);
	const status = statusState?.owner === owner ? statusState : null;
	const action = actionState?.owner === owner ? actionState : null;
	const submitting = submissionState?.owner === owner;
	const statusFresh = status?.revision === revision && status?.requestId === operation?.requestId && !status?.error;
	const data = status?.data;
	const result = matchingResult(data?.result, operation) ? data.result : null;
	const attempt = data?.attempt || null;
	const pending = Boolean(operation && !result);
	const requiredIds = data?.required_requirement_ids || [];
	const currentResult = Boolean(
		result &&
		project?.current_snapshots?.use_cases?.snapshot_id === result.snapshot_id &&
		project?.stage_state?.use_cases?.current_snapshot_id === result.snapshot_id &&
		!project.stage_state.use_cases.stale
	);
	const canStart = Boolean(
		scope && predecessorId && statusFresh && data?.enabled && data?.eligible && !data.blocked && !attempt && !action?.busy && !submitting
	);
	const canRetry = Boolean(
		canStart &&
		pending &&
		!operation.releaseRequested &&
		operation.predecessorId === predecessorId &&
		operation.body.base_project_revision === revision &&
		requiredIds.every((id) => operation.body.refresh_requirement_ids.includes(id))
	);
	const pathFor = (endpoint) => endpoint.path.replace("{project_id}", encodeURIComponent(projectId));

	const readStatus = async (requested, { release = null, failure = "" } = {}) => {
		if (!scope || activeOwner.current !== owner) return;
		owner.read?.abort();
		const controller = new AbortController();
		owner.read = controller;
		const requestedRevision = activeCallbacks.current.revision;
		const requestId = requested?.requestId;
		try {
			const endpoint = API_CONTRACT_ENDPOINTS.projectUseCasesRefreshStatus;
			const path = pathFor(endpoint);
			const query = requestId ? `?${new URLSearchParams({ request_id: requestId })}` : "";
			const response = await activeCallbacks.current.request(`${path}${query}`, { method: endpoint.method, signal: controller.signal });
			if (!response.ok) throw new Error(statusError);
			const next = await response.json();
			if (controller.signal.aborted || activeOwner.current !== owner) return;
			setStatus({ owner, revision: requestedRevision, requestId, data: next });
			const receipt = matchingResult(next.result, requested) ? next.result : null;
			const releaseUnconfirmed = release && next.attempt?.operation_id === release.operation_id;
			const error = releaseUnconfirmed ? "The release was not confirmed. Check the current attempt." : failure;
			if (receipt && (receipt.project_revision > requestedRevision || failure)) {
				const updated = await activeCallbacks.current.readProject();
				if (controller.signal.aborted || activeOwner.current !== owner) return;
				if (!updated || updated.current_revision < receipt.project_revision) {
					setAction({ owner, error: "The refresh was saved. Check scenario refresh status again to load the plan." });
					return;
				}
			} else if (requested && !next.result && !next.attempt && !next.blocked && (requested.releaseRequested || failure)) {
				if (owner.submission?.operation.requestId === requestId) {
					owner.submission = null;
					setSubmission(null);
				}
				recovery.clear();
			}
			if (owner.action || failure) setAction({ owner, error: receipt ? "" : error });
		} catch {
			if (!controller.signal.aborted && activeOwner.current === owner) {
				setStatus((previous) => ({ owner, data: previous?.owner === owner ? previous.data : null, error: statusError }));
				if (owner.action) setAction({ owner, error: "" });
			}
		} finally {
			if (owner.read === controller) owner.read = null;
		}
	};

	useEffect(() => {
		if (!scope || !(predecessorId || operation || attempt) || owner.action || owner.submission) return;
		void readStatus(operation);
		const controller = owner.read;
		return () => controller?.abort();
	}, [owner, scope, predecessorId, operation?.requestId, revision]);

	const check = async (release = null) => {
		if (!scope || owner.action || (release && (!statusFresh || release !== attempt))) return;
		const action = { done: null };
		owner.action = action;
		owner.read?.abort();
		setAction({ owner, busy: true });
		const requested = release && operation?.requestId === release.request_id ? { ...operation, releaseRequested: true } : operation;
		if (requested !== operation) recovery.setValue(requested);
		action.done = (async () => {
			try {
				if (release) {
					const endpoint = API_CONTRACT_ENDPOINTS.projectUseCasesRefreshRelease;
					const path = pathFor(endpoint).replace("{operation_id}", encodeURIComponent(release.operation_id));
					await activeCallbacks.current.request(path, {
						method: endpoint.method,
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({ fence: release.fence }),
					});
				}
			} catch {
				// The exact persisted state decides whether the release took effect.
			}
			await readStatus(requested, { release });
		})();
		try {
			await action.done;
		} finally {
			if (owner.action === action) owner.action = null;
		}
	};

	const submit = async (ids, retry = false) => {
		if (!canStart || owner.action || owner.submission || (retry ? !canRetry : pending)) return;
		if (!Array.isArray(ids) || new Set(ids).size !== ids.length || !requiredIds.every((id) => ids.includes(id))) return;
		const requested = retry
			? operation
			: { requestId: createRequestId(), predecessorId, body: { base_project_revision: revision, refresh_requirement_ids: ids } };
		const submission = { owner, operation: requested };
		owner.submission = submission;
		owner.read?.abort();
		recovery.setValue(requested);
		setAction(null);
		setSubmission(submission);
		let failure = "";
		try {
			const endpoint = API_CONTRACT_ENDPOINTS.projectUseCasesGenerate;
			const response = await activeCallbacks.current.request(pathFor(endpoint), {
				method: endpoint.method,
				headers: { "Content-Type": "application/json", "X-Request-ID": requested.requestId },
				body: JSON.stringify(requested.body),
			});
			if (!response.ok && response.status < 500) {
				const data = await response.json().catch(() => null);
				failure =
					(typeof data?.detail === "string" ? data.detail : data?.detail?.message) ||
					"The scoped refresh was declined. The prior plan is still saved.";
			}
		} catch {
			// A response loss never starts a second refresh. Check the exact request.
		} finally {
			await owner.action?.done;
			if (activeOwner.current === owner && owner.submission === submission) {
				await readStatus(requested, { failure });
				if (owner.submission === submission) owner.submission = null;
				setSubmission((previous) => (previous === submission ? null : previous));
			}
		}
	};
	return {
		enabled: data?.enabled === true,
		legacy: statusFresh && data?.enabled === false,
		eligible: statusFresh && data?.enabled === true && data?.eligible === true,
		blocked: data?.blocked === true,
		canStart: canStart && !pending,
		canRetry,
		checking: action?.busy === true,
		submitting,
		attempt,
		statusFresh,
		pending,
		result,
		currentResult,
		requiredIds,
		error: status?.error || action?.error || recovery.error,
		check: () => check(),
		release: () => attempt && check(attempt),
		submit,
		retry: () => canRetry && submit(operation.body.refresh_requirement_ids, true),
		dismiss:
			pending &&
			statusFresh &&
			!data?.result &&
			!attempt &&
			!data?.blocked &&
			(operation.predecessorId !== predecessorId || operation.body.base_project_revision !== revision || operation.releaseRequested)
				? recovery.clear
				: null,
	};
}
