import { useCallback, useEffect, useRef, useState } from "react";
import useReviewDraft from "./useReviewDraft";
import { API_CONTRACT_ENDPOINTS } from "../api/generated/api-contracts";
import { createRequestId, parseApiError } from "../services/apiClient";

const POLL_DELAY_MS = 2000;
const MAX_AUTOMATIC_CHECKS = 10;
const ACTIVE_STATES = ["queued", "running"];
const SAVED_PLAN_GENERATION = "saved_plan_generation";

export default function useApprovalContinuation({ enabled, projectId, identity, request, savedPlanSnapshotId }) {
	const scope = enabled ? JSON.stringify([identity, projectId]) : "";
	const projectSegment = encodeURIComponent(projectId || "");
	const basePath = API_CONTRACT_ENDPOINTS.approvalContinuations.path.replace("{project_id}", projectSegment);
	const pending = useReviewDraft({
		identity: enabled ? identity : "",
		projectId,
		snapshotId: "scope-approval-v1",
		name: "pending-scope-approval",
		initialValue: null,
		// Two tabs can submit different approval keys from the same revision.
		perTab: true,
	});
	const requestRef = useRef(request);
	requestRef.current = request;
	const activeScope = useRef(scope);
	activeScope.current = scope;
	const inFlight = useRef(null);
	const pollCount = useRef(0);
	const cancelInFlight = useRef(null);
	const retryInFlight = useRef(null);
	const savedPlanInFlight = useRef(null);
	const latestRead = useRef(null);
	const savedReviewInFlight = useRef(null);
	const [readState, setReadState] = useState(null);
	const [actionState, setActionState] = useState(null);
	const [savedReviewState, setSavedReviewState] = useState(null);
	const current = readState?.scope === scope ? readState : null;
	const action = actionState?.scope === scope ? actionState : null;
	const savedReviewAction = savedReviewState?.scope === scope ? savedReviewState : null;
	const savedReviewAttempt = current?.data?.saved_review_attempt || null;
	const runs = current?.data?.runs || [];
	latestRead.current = current;
	const savedPlanOffer = current?.data?.saved_plan_generation;
	const requestId = pending.value?.requestId || "";
	const pendingStage = pending.value?.stage || "prepare_scenarios";
	const savedPlanPending = pending.value?.source === SAVED_PLAN_GENERATION;
	const activeRequestId = useRef(requestId);
	activeRequestId.current = requestId;
	const match =
		requestId &&
		runs.find(
			(item) =>
				item.request_id === requestId &&
				item.stage === pendingStage &&
				(!savedPlanPending || item.source_snapshot_id === pending.value.snapshotId)
		);
	const readReceipt = current?.data?.receipt;
	const receipt =
		requestId && readReceipt?.request_id === requestId && (!savedPlanPending || match?.run_id === readReceipt.run_id) ? readReceipt : null;
	const exactSavedPlanRun =
		savedPlanSnapshotId && runs.find((item) => item.stage === "generate_tests" && item.source_snapshot_id === savedPlanSnapshotId);
	const competingSavedPlanRun =
		savedPlanPending &&
		requestId &&
		!match &&
		current?.requestedId === requestId &&
		!current.error &&
		readReceipt === null &&
		runs.find(
			(item) =>
				item.stage === "generate_tests" &&
				item.source_snapshot_id === pending.value.snapshotId &&
				item.run_id &&
				item.request_id &&
				item.request_id !== requestId
		);
	const run = match || competingSavedPlanRun || (!requestId ? exactSavedPlanRun || runs[0] : null);
	const checkingTests = (requestId ? pendingStage : run?.stage) === "generate_tests";
	const unresolved = Boolean(requestId && !match && !receipt && !competingSavedPlanRun);

	const refresh = useCallback(
		async ({ reset = false, replace = false } = {}) => {
			if (!scope) return null;
			const existing = inFlight.current;
			if (existing?.scope === scope) {
				if (!replace) return null;
				existing.controller.abort();
			}
			if (reset) pollCount.current = 0;
			const controller = new AbortController();
			const read = { scope, controller };
			inFlight.current = read;
			const query = new URLSearchParams();
			if (requestId) query.set("request_id", requestId);
			if (requestId && pendingStage === "generate_tests") query.set("stage", pendingStage);
			const path = requestId ? `${basePath}?${query}` : basePath;
			try {
				const response = await requestRef.current(path, { method: "GET", signal: controller.signal });
				if (!response.ok) throw new Error("Approval continuation status is unavailable.");
				const data = await response.json();
				if (!controller.signal.aborted && activeScope.current === scope) {
					setReadState({ scope, requestedId: requestId, data, error: "" });
					return data;
				}
			} catch {
				if (!controller.signal.aborted && activeScope.current === scope)
					setReadState((previous) => ({
						scope,
						data: previous?.scope === scope ? previous.data : null,
						error: "Could not check approval status. Check status again.",
					}));
			} finally {
				if (inFlight.current === read) inFlight.current = null;
			}
			return null;
		},
		[basePath, pendingStage, requestId, scope]
	);

	useEffect(() => {
		void refresh({ reset: true });
		return () => {
			const active = inFlight.current;
			if (active?.scope === scope) {
				active.controller.abort();
				inFlight.current = null;
			}
		};
	}, [refresh, scope]);

	useEffect(() => {
		if (!scope || !current || (!unresolved && !ACTIVE_STATES.includes(run?.state)) || pollCount.current >= MAX_AUTOMATIC_CHECKS)
			return undefined;
		const timer = window.setTimeout(() => {
			pollCount.current += 1;
			void refresh();
		}, POLL_DELAY_MS);
		return () => window.clearTimeout(timer);
	}, [current, refresh, run?.state, scope, unresolved]);

	const clearPending = (requestId) => pending.clear({ matches: (value) => value?.requestId === requestId });
	useEffect(() => {
		// A different tab can enroll this exact plan without advancing the project revision.
		if (competingSavedPlanRun) clearPending(requestId);
	}, [competingSavedPlanRun, requestId]);

	const check = async (readProject) => {
		if (!requestId) {
			void refresh({ reset: true });
			if (run?.result_snapshot_id) await readProject();
			return;
		}
		const isCurrent = () => activeScope.current === scope && activeRequestId.current === requestId;
		const project = await readProject();
		if (!isCurrent()) return;
		// A read made after the newer revision sees any receipt committed by the old request.
		const status = await refresh({ reset: true, replace: true });
		if (
			isCurrent() &&
			project?.project_id === projectId &&
			Number.isInteger(project.current_revision) &&
			Number.isInteger(pending.value?.baseRevision) &&
			project.current_revision > pending.value.baseRevision &&
			status?.receipt === null &&
			Array.isArray(status.runs) &&
			!status.runs.some((item) => item.request_id === requestId && item.stage === pendingStage)
		) {
			clearPending(requestId);
		}
	};
	const accept = (response, requestId, stage = "prepare_scenarios") => {
		const result = response.continuation;
		if (result) {
			setReadState((previous) => {
				const prior = previous?.scope === scope ? previous.data : null;
				return {
					scope,
					error: "",
					data: {
						...prior,
						enabled: true,
						tests_enabled: stage === "generate_tests" || prior?.tests_enabled,
						runs: [result, ...(prior?.runs || []).filter((item) => item.run_id !== result.run_id)],
					},
				};
			});
		}
		setActionState({ scope, stage, error: "", blockedReason: response.blocked_reason || "" });
		clearPending(requestId);
	};
	const startSavedPlanGeneration = async (template) => {
		if (
			!scope ||
			!savedPlanOffer ||
			current.error ||
			current.data.tests_enabled !== true ||
			exactSavedPlanRun ||
			savedPlanInFlight.current?.scope === scope
		)
			return;
		const { base_project_revision: baseRevision, snapshot_id: snapshotId, review_id: reviewId } = savedPlanOffer;
		const prior = unresolved ? pending.value : null;
		if (
			prior &&
			(!savedPlanPending || prior.baseRevision !== baseRevision || prior.snapshotId !== snapshotId || prior.reviewId !== reviewId)
		)
			return;
		const key = prior?.requestId || createRequestId();
		const selectedTemplate = prior ? prior.template : template;
		const mutation = { scope };
		const isCurrent = () => activeScope.current === scope && activeRequestId.current === key && savedPlanInFlight.current === mutation;
		savedPlanInFlight.current = mutation;
		activeRequestId.current = key;
		setActionState({ scope, savedPlanSubmitting: true });
		pending.setValue({
			requestId: key,
			stage: "generate_tests",
			source: SAVED_PLAN_GENERATION,
			baseRevision,
			snapshotId,
			reviewId,
			template: selectedTemplate,
		});
		try {
			const endpoint = API_CONTRACT_ENDPOINTS.generateTestsFromSavedApproval;
			const response = await requestRef.current(endpoint.path.replace("{project_id}", projectSegment), {
				method: endpoint.method,
				headers: { "Content-Type": "application/json", "X-Request-ID": key },
				body: JSON.stringify({
					base_project_revision: baseRevision,
					snapshot_id: snapshotId,
					review_id: reviewId,
					template: selectedTemplate,
				}),
			});
			if (!response.ok) {
				if (response.status >= 500) return;
				const error = await parseApiError(response, "Could not queue tests from this saved approval.");
				if (!isCurrent()) return;
				clearPending(key);
				setActionState({ scope, savedPlanError: error });
				return "rejected";
			}
			const { continuation } = await response.json();
			if (
				!isCurrent() ||
				!continuation?.run_id ||
				continuation?.stage !== "generate_tests" ||
				continuation.request_id !== key ||
				continuation.source_snapshot_id !== snapshotId
			)
				return;
			const recovered = latestRead.current?.data?.runs?.find(
				(item) =>
					item.run_id === continuation.run_id &&
					item.request_id === key &&
					item.stage === "generate_tests" &&
					item.source_snapshot_id === snapshotId
			);
			accept({ continuation: recovered || continuation }, key, "generate_tests");
		} catch {
			// The request may have committed. The saved request identity lets status recover it.
		} finally {
			if (savedPlanInFlight.current === mutation) savedPlanInFlight.current = null;
			if (activeScope.current === scope) {
				setActionState((previous) => (previous?.scope === scope && previous.savedPlanSubmitting ? null : previous));
			}
		}
	};
	const cancel = async () => {
		if (!scope || !run || cancelInFlight.current?.scope === scope) return;
		const mutation = { scope };
		cancelInFlight.current = mutation;
		setActionState({ scope, cancelling: true, error: "" });
		try {
			const cancelPath = API_CONTRACT_ENDPOINTS.approvalContinuationCancel.path
				.replace("{project_id}", projectSegment)
				.replace("{run_id}", encodeURIComponent(run.run_id));
			const response = await requestRef.current(cancelPath, { method: API_CONTRACT_ENDPOINTS.approvalContinuationCancel.method });
			if (!response.ok)
				throw new Error(
					run.state === "interrupted"
						? "Could not release the interrupted attempt. Check the current status."
						: checkingTests
							? "Could not cancel test generation. Check the current status."
							: "Could not cancel scenario preparation. Check the current status."
				);
			const cancelled = await response.json();
			if (activeScope.current !== scope) return;
			if (inFlight.current?.scope === scope) {
				inFlight.current.controller.abort();
				inFlight.current = null;
			}
			setReadState((previous) =>
				previous?.scope === scope
					? {
							...previous,
							data: { ...previous.data, runs: previous.data.runs.map((item) => (item.run_id === cancelled.run_id ? cancelled : item)) },
						}
					: previous
			);
			await refresh({ reset: true });
		} catch (error) {
			if (activeScope.current === scope) setActionState({ scope, error: error.message });
		} finally {
			if (cancelInFlight.current === mutation) cancelInFlight.current = null;
			if (activeScope.current === scope)
				setActionState((previous) => (previous?.scope === scope ? { ...previous, cancelling: false } : previous));
		}
	};
	const retry = async () => {
		if (!scope || !run?.retry_available || retryInFlight.current?.scope === scope) return;
		const mutation = { scope };
		retryInFlight.current = mutation;
		setActionState({ scope, retrying: true, error: "" });
		let rejection = "";
		try {
			const endpoint = API_CONTRACT_ENDPOINTS.approvalContinuationRetry;
			const path = endpoint.path.replace("{project_id}", projectSegment).replace("{run_id}", encodeURIComponent(run.run_id));
			try {
				const response = await requestRef.current(path, { method: endpoint.method });
				if (!response.ok) {
					const { detail } = await response.json();
					if (typeof detail === "string") rejection = detail;
				}
			} catch {
				// A lost reply can follow a committed retry. Read the original run before offering another action.
			}
			if (activeScope.current !== scope) return;
			const status = await refresh({ reset: true, replace: true });
			const recovered = status?.runs?.find((item) => item.run_id === run.run_id);
			if (activeScope.current === scope && (!recovered || recovered.retry_available)) {
				setActionState({ scope, error: rejection || "Could not confirm the retry. Check the current status before trying again." });
			}
		} finally {
			if (retryInFlight.current === mutation) retryInFlight.current = null;
			if (activeScope.current === scope)
				setActionState((previous) => (previous?.scope === scope ? { ...previous, retrying: false } : previous));
		}
	};

	const reconcileSavedReview = async (readProject, attempt = null) => {
		if (!scope || savedReviewInFlight.current?.scope === scope) return;
		const mutation = { scope };
		savedReviewInFlight.current = mutation;
		setSavedReviewState({ scope, mutation, busy: true });
		let releaseUnconfirmed = false;
		try {
			if (attempt) {
				try {
					const endpoint = API_CONTRACT_ENDPOINTS.savedReviewAdmissionRelease;
					const path = endpoint.path
						.replace("{project_id}", projectSegment)
						.replace("{operation_id}", encodeURIComponent(attempt.operation_id));
					const response = await requestRef.current(path, {
						method: endpoint.method,
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({ fence: attempt.fence }),
					});
					releaseUnconfirmed = !response.ok || (await response.json()).released !== true;
				} catch {
					releaseUnconfirmed = true;
				}
			}
			if (activeScope.current !== scope) return;
			const status = await refresh({ reset: true, replace: true });
			if (!status || activeScope.current !== scope || !(await readProject()))
				throw new Error("Could not confirm saved review status. Check status again.");
			const error = releaseUnconfirmed && status.saved_review_attempt ? "The release was not confirmed. Check the current attempt." : "";
			if (activeScope.current === scope) setSavedReviewState({ scope, error });
		} catch (error) {
			if (activeScope.current === scope) setSavedReviewState({ scope, error: error.message });
		} finally {
			if (savedReviewInFlight.current === mutation) savedReviewInFlight.current = null;
			setSavedReviewState((previous) => (previous?.mutation === mutation ? null : previous));
		}
	};

	return {
		available: current?.data?.enabled === true,
		testsAvailable: current?.data?.tests_enabled === true,
		runs,
		run,
		stage: action?.blockedReason ? action.stage : requestId ? pendingStage : run?.stage || "prepare_scenarios",
		active: ACTIVE_STATES.includes(run?.state),
		unresolved,
		receipt,
		blockedReason: action?.blockedReason || receipt?.blocked_reason || "",
		error: action?.error || current?.error || pending.error,
		pendingRequest: pending.value,
		cancelling: action?.cancelling === true,
		retrying: action?.retrying === true,
		begin: (value) => {
			setActionState(null);
			pending.setValue(value);
		},
		clearPending,
		accept,
		cancel,
		retry,
		check,
		savedPlanGeneration: {
			offer: savedPlanOffer,
			pending: savedPlanPending,
			submitting: action?.savedPlanSubmitting === true && savedPlanPending && !match && !competingSavedPlanRun,
			error: action?.savedPlanError || "",
			start: startSavedPlanGeneration,
		},
		savedReview: {
			enabled: current?.data?.saved_review_enabled === true,
			statusRead: Boolean(current?.data),
			statusError: current?.error || "",
			error: savedReviewAction?.error || "",
			busy: savedReviewAction?.busy === true,
			attempt: savedReviewAttempt,
			check: reconcileSavedReview,
			release: (readProject) => savedReviewAttempt && reconcileSavedReview(readProject, savedReviewAttempt),
		},
	};
}
