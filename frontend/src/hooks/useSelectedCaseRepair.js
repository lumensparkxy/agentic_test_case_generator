import { useCallback, useEffect, useRef, useState } from "react";
import { API_CONTRACT_ENDPOINTS } from "../api/generated/api-contracts";
import { createRequestId, parseApiError } from "../services/apiClient";
import useReviewDraft from "./useReviewDraft";

function readOperation(value) {
	const payload = value?.payload;
	return value?.requestId &&
		payload?.snapshot_id &&
		payload.case_id &&
		payload.case_version_id &&
		payload.target_id &&
		Number.isInteger(payload.base_project_revision)
		? value
		: null;
}

function exactResult(result, operation) {
	return Boolean(
		operation &&
		result?.request_id === operation.requestId &&
		result.source_snapshot_id === operation.payload.snapshot_id &&
		result.case_id === operation.payload.case_id &&
		result.before_case_version_id === operation.payload.case_version_id &&
		result.after_case_version_id &&
		result.after_case_version_id !== result.before_case_version_id &&
		result.result_snapshot_id &&
		result.result_snapshot_id !== result.source_snapshot_id &&
		Number.isInteger(result.project_revision) &&
		result.project_revision > operation.payload.base_project_revision &&
		Number.isInteger(result.preserved_case_count) &&
		result.preserved_case_count >= 0
	);
}

export default function useSelectedCaseRepair({
	enabled,
	identity,
	projectId,
	snapshotId,
	caseId,
	caseVersionId,
	revision,
	request,
	readProject,
}) {
	const scope = enabled ? JSON.stringify([identity, projectId]) : "";
	const recovery = useReviewDraft({
		identity: enabled ? identity : "",
		projectId,
		snapshotId: "selected-case-repair-v1",
		name: "selected-case-repair",
		initialValue: null,
	});
	const operation = readOperation(recovery.value);
	const selectedCase = JSON.stringify([snapshotId, caseId, caseVersionId, revision]);
	const requestRef = useRef(request);
	requestRef.current = request;
	const context = useRef(null);
	if (context.current?.scope !== scope) context.current = { scope, read: null, mutation: null, submission: null };
	const active = context.current;
	const latest = useRef(null);
	latest.current = { snapshotId, caseId, selectedCase };
	const [statusState, setStatus] = useState(null);
	const [actionState, setAction] = useState(null);
	const [submissionState, setSubmission] = useState(null);
	const [confirmedState, setConfirmed] = useState(null);
	const status = statusState?.context === active ? statusState : null;
	const action = actionState?.context === active ? actionState : null;
	const submitting = submissionState?.context === active;
	const confirmed = confirmedState?.context === active && exactResult(confirmedState.result, operation) ? confirmedState.result : null;
	const pathFor = useCallback((endpoint) => endpoint.path.replace("{project_id}", encodeURIComponent(projectId)), [projectId]);

	const checkStatus = useCallback(
		async (targetOperation, selection = latest.current) => {
			if (!scope || context.current !== active) return null;
			active.read?.abort();
			const controller = new AbortController();
			active.read = controller;
			const query = new URLSearchParams();
			if (selection.snapshotId && selection.caseId) {
				query.set("snapshot_id", selection.snapshotId);
				query.set("case_id", selection.caseId);
			}
			const separateReceipt = Boolean(
				query.size &&
				targetOperation &&
				(targetOperation.payload.snapshot_id !== selection.snapshotId || targetOperation.payload.case_id !== selection.caseId)
			);
			if (targetOperation?.requestId && !separateReceipt) query.set("request_id", targetOperation.requestId);
			const endpoint = API_CONTRACT_ENDPOINTS.selectedCaseRepairStatus;
			const path = pathFor(endpoint);
			let missingEndpoint = false;
			const read = async (params) => {
				const response = await requestRef.current(params.size ? `${path}?${params}` : path, {
					method: endpoint.method,
					signal: controller.signal,
				});
				if (response.status === 404) missingEndpoint = true;
				if (!response.ok) throw new Error("Could not check selected repair status. Check status again.");
				return response.json();
			};
			try {
				const [data, receiptStatus] = await Promise.all([
					read(query),
					separateReceipt ? read(new URLSearchParams({ request_id: targetOperation.requestId })) : null,
				]);
				if (receiptStatus) data.result = receiptStatus.result;
				if (!controller.signal.aborted && context.current === active) {
					setStatus({ context: active, selectedCase: selection.selectedCase, requestId: targetOperation?.requestId, data });
					return data;
				}
			} catch {
				if (!controller.signal.aborted && context.current === active)
					setStatus((previous) => ({
						context: active,
						data: previous?.context === active ? previous.data : null,
						error: missingEndpoint
							? "Selected repair is unavailable for this project. Check status again."
							: "Could not check selected repair status. Check status again.",
					}));
			} finally {
				if (active.read === controller) active.read = null;
			}
			return null;
		},
		[scope, active, pathFor]
	);

	useEffect(() => {
		if (!scope || active.mutation || active.submission) return;
		void checkStatus(operation);
		const controller = active.read;
		return () => controller?.abort();
	}, [active, checkStatus, operation?.requestId, scope, selectedCase]);

	const statusFresh = status?.selectedCase === selectedCase && status?.requestId === operation?.requestId;
	const data = status?.data;
	const attempt = data?.attempt || null;
	const result = confirmed || (exactResult(data?.result, operation) ? data.result : null);
	const checking = Boolean(action?.busy);
	const pending = Boolean(operation && !result);
	const targets =
		statusFresh && caseVersionId && data?.enabled === true
			? (data.targets || []).filter((target) => target.case_id === caseId && target.case_version_id === caseVersionId)
			: [];
	const canRetry = Boolean(
		pending &&
		!operation.releaseRequested &&
		operation.payload.snapshot_id === snapshotId &&
		operation.payload.case_id === caseId &&
		operation.payload.base_project_revision === revision &&
		targets.some(
			(target) => target.target_id === operation.payload.target_id && target.case_version_id === operation.payload.case_version_id
		)
	);
	const cannotRetry = Boolean(
		pending &&
		statusFresh &&
		!attempt &&
		!data?.blocked &&
		(operation.payload.snapshot_id !== snapshotId ||
			operation.payload.base_project_revision !== revision ||
			operation.releaseRequested ||
			data?.enabled === false)
	);

	const reconcile = async (targetOperation, { release = null, failure = "", submittedResult = null } = {}) => {
		const next = await checkStatus(targetOperation);
		if (context.current !== active) return;
		const saved = exactResult(next?.result, targetOperation) ? next.result : submittedResult;
		if (saved) {
			setConfirmed({ context: active, result: saved });
			const project = await readProject();
			if (context.current === active)
				setAction({ context: active, error: project ? "" : "Repair is saved. Check status again to load the current suite." });
			return;
		}
		const releaseUnconfirmed = release && (!next || next.attempt?.operation_id === release.operation_id);
		if (targetOperation && next && !next.result && !next.attempt && !next.blocked && (failure || targetOperation.releaseRequested)) {
			if (active.submission?.operation.requestId === targetOperation.requestId) {
				active.submission = null;
				setSubmission(null);
			}
			recovery.clear();
		}
		setAction({ context: active, error: releaseUnconfirmed ? "The release was not confirmed. Check the current attempt." : failure });
	};

	const check = async () => {
		if (!scope || active.mutation) return;
		active.mutation = "check";
		setAction({ context: active, busy: true });
		try {
			await reconcile(operation, { submittedResult: result });
		} finally {
			active.mutation = null;
		}
	};

	const submit = async (target) => {
		if (
			!scope ||
			active.mutation ||
			active.submission ||
			!statusFresh ||
			data?.blocked ||
			attempt ||
			!targets.includes(target) ||
			(pending && (!canRetry || target.target_id !== operation.payload.target_id))
		)
			return;
		const submitted = pending
			? operation
			: {
					requestId: createRequestId(),
					payload: {
						snapshot_id: snapshotId,
						base_project_revision: revision,
						case_id: caseId,
						case_version_id: caseVersionId,
						target_id: target.target_id,
					},
				};
		const submission = { context: active, operation: submitted };
		active.submission = submission;
		active.read?.abort();
		recovery.setValue(submitted);
		setSubmission(submission);
		setAction(null);
		let failure = "";
		let submittedResult = null;
		try {
			const endpoint = API_CONTRACT_ENDPOINTS.selectedCaseRepair;
			const response = await requestRef.current(pathFor(endpoint), {
				method: endpoint.method,
				headers: { "Content-Type": "application/json", "X-Request-ID": submitted.requestId },
				body: JSON.stringify(submitted.payload),
			});
			if (response.ok) {
				const returned = await response.json();
				if (exactResult(returned, submitted)) submittedResult = returned;
			} else if (response.status < 500) failure = await parseApiError(response, "The selected repair was declined.");
		} catch {
			// The provider or publication may have completed after the response was lost.
		} finally {
			if (context.current === active && active.submission === submission) {
				try {
					if (!active.mutation) {
						active.mutation = "settle";
						try {
							await reconcile(submitted, { failure, submittedResult });
						} finally {
							active.mutation = null;
						}
					} else if (submittedResult) {
						setConfirmed({ context: active, result: submittedResult });
						await readProject();
					}
				} finally {
					if (active.submission === submission) active.submission = null;
					setSubmission((previous) => (previous === submission ? null : previous));
				}
			}
		}
	};

	const release = async () => {
		if (!scope || !attempt || !statusFresh || active.mutation) return;
		active.mutation = "release";
		active.read?.abort();
		const releasing = operation?.requestId === attempt.request_id ? { ...operation, releaseRequested: true } : operation;
		if (releasing !== operation) recovery.setValue(releasing);
		setAction({ context: active, busy: true });
		try {
			const endpoint = API_CONTRACT_ENDPOINTS.selectedCaseRepairRelease;
			const path = pathFor(endpoint).replace("{operation_id}", encodeURIComponent(attempt.operation_id));
			await requestRef.current(path, {
				method: endpoint.method,
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ fence: attempt.fence }),
			});
		} catch {
			// Read the fenced state before allowing another deliberate repair.
		} finally {
			if (context.current === active) await reconcile(releasing, { release: attempt, submittedResult: result });
			active.mutation = null;
		}
	};

	return {
		enabled: data?.enabled === true,
		legacy: data?.enabled === false && !status?.error,
		statusFresh,
		error: status?.error || action?.error || recovery.error,
		checking,
		submitting,
		attempt,
		result,
		pending,
		canRetry,
		pendingTarget: operation?.payload.target_id,
		blocked: data?.blocked === true,
		targets,
		check,
		submit,
		release,
		dismiss: cannotRetry ? () => recovery.clear() : null,
	};
}
