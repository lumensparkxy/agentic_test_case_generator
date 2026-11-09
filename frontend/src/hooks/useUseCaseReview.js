import useReviewDraft from "./useReviewDraft";
import { useCallback, useEffect, useRef, useState } from "react";

import { createRequestId } from "../services/apiClient";
import { submitUseCaseReviewDecision } from "../services/useCaseReviewClient";

const initialOutcome = () => ({
	status: "idle",
	error: "",
	conflict: null,
	response: null,
	announcement: "",
});

const reviewFingerprint = ({ identity, projectId, snapshotId, baseProjectRevision, decision, comment, scenarioReviews, generateTests }) =>
	JSON.stringify({
		identity,
		projectId,
		snapshotId,
		baseProjectRevision,
		decision,
		scenarioReviews,
		generateTests,
		comment: `${comment || ""}`.trim(),
	});

export default function useUseCaseReview({
	request,
	identity = "",
	projectId = "",
	snapshotId = "",
	baseProjectRevision = null,
	onCommitted = null,
	onReload = null,
	continuation = null,
} = {}) {
	const requestRef = useRef(request);
	const callbacksRef = useRef({ onCommitted, onReload, continuation });
	const activeRequestRef = useRef({ sequence: 0, scope: "", phase: "idle" });
	const retryIdentityRef = useRef(null);
	const lastSubmissionRef = useRef({});
	const submittingRef = useRef(false);
	const projectScopeRef = useRef("");
	const requestScopeRef = useRef("");
	const scopeContextRef = useRef(null);
	const recovery = useReviewDraft({
		identity,
		projectId,
		snapshotId,
		name: "use-case-decision",
		initialValue: { decision: "", comment: "", revision: baseProjectRevision },
	});
	const { decision, comment } = recovery.value;
	const recoveryRef = useRef(recovery);
	recoveryRef.current = recovery;
	const [outcome, setOutcome] = useState(initialOutcome);

	requestRef.current = request;
	callbacksRef.current = { onCommitted, onReload, continuation };
	const projectScope = `${identity}::${projectId}`;
	const requestScope = `${projectScope}::${snapshotId}::${baseProjectRevision ?? ""}`;
	projectScopeRef.current = projectScope;
	requestScopeRef.current = requestScope;

	useEffect(() => {
		const previousScope = scopeContextRef.current;
		const projectChanged = !previousScope || previousScope.projectScope !== projectScope;
		const requestChanged = !previousScope || previousScope.requestScope !== requestScope;
		const snapshotChanged = Boolean(previousScope && previousScope.snapshotId !== snapshotId);
		const isCommittedRefresh = activeRequestRef.current.phase === "committed" && !projectChanged;
		scopeContextRef.current = { projectScope, requestScope, snapshotId };

		if (isCommittedRefresh) {
			activeRequestRef.current = { ...activeRequestRef.current, scope: requestScope };
		} else if (requestChanged) {
			activeRequestRef.current = {
				sequence: activeRequestRef.current.sequence + 1,
				scope: requestScope,
				phase: "idle",
			};
			submittingRef.current = false;
		}
		retryIdentityRef.current = null;

		if (projectChanged) {
			setOutcome(initialOutcome());
			return;
		}
		if (!requestChanged || isCommittedRefresh) {
			return;
		}
		setOutcome((current) => ({
			status: current.status === "success" && !snapshotChanged ? "success" : "idle",
			error: "",
			conflict: null,
			response: null,
			announcement: snapshotChanged
				? current.status === "success"
					? "Decision saved for the reviewed artifact. A newer Use Cases version is ready for review."
					: "A newer Use Cases artifact is loaded. Review it before submitting."
				: current.status === "success"
					? current.announcement
					: "The latest project revision is loaded. Review your decision before submitting.",
		}));
	}, [projectScope, requestScope, snapshotId]);

	useEffect(
		() => () => {
			activeRequestRef.current = {
				sequence: activeRequestRef.current.sequence + 1,
				scope: "unmounted",
				phase: "idle",
			};
			projectScopeRef.current = "unmounted";
			requestScopeRef.current = "unmounted";
			submittingRef.current = false;
		},
		[]
	);

	const recoveredApproval =
		continuation?.pendingRequest?.stage === "generate_tests" &&
		continuation.pendingRequest.requestId === retryIdentityRef.current?.requestId &&
		!continuation.unresolved &&
		Boolean(continuation.receipt || continuation.run);
	useEffect(() => {
		if (!recoveredApproval || !["error", "conflict"].includes(outcome.status)) return;
		retryIdentityRef.current = null;
		recoveryRef.current.clear({ keepVisible: true });
		setOutcome({ ...initialOutcome(), status: "success", announcement: "Use Cases approved." });
	}, [outcome.status, recoveredApproval]);

	const setDecision = useCallback(
		(nextDecision) => {
			recoveryRef.current.setValue((old) => ({ ...old, decision: nextDecision, revision: baseProjectRevision }));
			retryIdentityRef.current = null;
			setOutcome((current) => (current.status === "error" ? { ...current, error: "", status: "idle" } : current));
		},
		[baseProjectRevision]
	);

	const setComment = useCallback(
		(nextComment) => {
			recoveryRef.current.setValue((old) => ({ ...old, comment: nextComment, revision: baseProjectRevision }));
			retryIdentityRef.current = null;
			setOutcome((current) => (current.status === "error" ? { ...current, error: "", status: "idle" } : current));
		},
		[baseProjectRevision]
	);

	const submit = useCallback(
		async (options = {}) => {
			const scenarioReviews = options.scenarioReviews;
			if (!scenarioReviews && recoveryRef.current.value.revision !== baseProjectRevision) {
				setOutcome({
					status: "conflict",
					error: "This draft belongs to an older project revision. Reload and review your decision before saving.",
					conflict: { reload_required: true },
					response: null,
					announcement: "Review the current source before saving.",
				});
				return null;
			}
			const submittedDecision = scenarioReviews ? "review_scenarios" : decision;
			lastSubmissionRef.current = options;
			if (submittingRef.current || !["approve", "request_changes", "review_scenarios"].includes(submittedDecision)) {
				return null;
			}
			const normalizedComment = scenarioReviews ? "" : `${comment || ""}`.trim();
			const generateTests = options.generateTests === true && submittedDecision === "approve" && Boolean(callbacksRef.current.continuation);
			if (submittedDecision === "request_changes" && !normalizedComment) {
				setOutcome({
					status: "error",
					error: "Add a comment describing the requested changes.",
					conflict: null,
					response: null,
					announcement: "",
				});
				return null;
			}

			const scope = requestScope;
			const sequence = activeRequestRef.current.sequence + 1;
			activeRequestRef.current = { sequence, scope, phase: "pending" };
			const fingerprint = reviewFingerprint({
				identity,
				projectId,
				snapshotId,
				baseProjectRevision,
				decision: submittedDecision,
				scenarioReviews,
				comment: normalizedComment,
				generateTests,
			});
			const pending = callbacksRef.current.continuation?.pendingRequest;
			const pendingOutcome = callbacksRef.current.continuation?.unresolved;
			if (pendingOutcome && (!generateTests || pending?.fingerprint !== fingerprint)) {
				setOutcome({
					...initialOutcome(),
					status: "error",
					error: "Check the pending approval or retry it with the original decision and comment.",
				});
				return null;
			}
			if (retryIdentityRef.current?.fingerprint !== fingerprint) {
				retryIdentityRef.current = { fingerprint, requestId: pending?.fingerprint === fingerprint ? pending.requestId : createRequestId() };
			}
			const requestId = retryIdentityRef.current.requestId;
			const isCurrent = () =>
				activeRequestRef.current.sequence === sequence &&
				activeRequestRef.current.scope === scope &&
				projectScopeRef.current === projectScope &&
				requestScopeRef.current === scope;
			const isProjectCurrent = () => activeRequestRef.current.sequence === sequence && projectScopeRef.current === projectScope;

			submittingRef.current = true;
			if (generateTests) {
				callbacksRef.current.continuation.begin({ requestId, fingerprint, baseRevision: baseProjectRevision, stage: "generate_tests" });
			}
			setOutcome({ status: "submitting", error: "", conflict: null, response: null, announcement: "Saving review decision…" });
			try {
				const response = await submitUseCaseReviewDecision(requestRef.current, {
					projectId,
					snapshotId,
					baseProjectRevision,
					decision: submittedDecision,
					scenarioReviews,
					comment: normalizedComment,
					requestId,
					generateTests,
				});
				if (!isCurrent()) {
					return null;
				}
				if (generateTests) callbacksRef.current.continuation.accept(response, "generate_tests");
				retryIdentityRef.current = null;
				recoveryRef.current.clear({ keepVisible: true });
				activeRequestRef.current = { ...activeRequestRef.current, phase: "committed" };
				setOutcome({
					status: "submitting",
					error: "",
					conflict: null,
					response: null,
					announcement: "Decision saved. Refreshing project and workspace state…",
				});
				try {
					const onCommitted = callbacksRef.current.onCommitted;
					const refreshedProject = await onCommitted?.(response, {
						projectId,
						snapshotId,
						baseProjectRevision,
						decision: submittedDecision,
						comment: normalizedComment,
					});
					if (!isProjectCurrent()) {
						return null;
					}
					if (typeof onCommitted === "function" && !refreshedProject) {
						throw new Error("The decision was saved, but the latest project and workspace state could not be refreshed.");
					}
					const newerArtifactLoaded = scopeContextRef.current?.snapshotId !== snapshotId;
					setOutcome({
						status: "success",
						error: "",
						conflict: null,
						response: null,
						announcement: newerArtifactLoaded
							? "Decision saved for the reviewed artifact. A newer Use Cases version is ready for review."
							: submittedDecision === "approve"
								? "Use Cases approved."
								: submittedDecision === "review_scenarios"
									? "Scenario reviews saved."
									: "Changes requested for Use Cases.",
					});
				} catch (refreshError) {
					if (!isProjectCurrent()) {
						return null;
					}
					setOutcome({
						status: "refresh_error",
						error: refreshError?.message || "The decision was saved, but the latest project and workspace state could not be refreshed.",
						conflict: null,
						response,
						announcement: "Decision saved. Reload the latest state before making another decision.",
					});
				}
				return response;
			} catch (error) {
				if (!isCurrent()) {
					return null;
				}
				if (generateTests && error?.status >= 400 && error.status < 500 && error.status !== 409) {
					callbacksRef.current.continuation.clearPending();
				}
				if (error?.status === 409) {
					setOutcome({
						status: "conflict",
						error: error.message,
						conflict: error.conflict || { reload_required: true },
						response: null,
						announcement: "The Use Cases artifact changed. Reload the latest version before submitting again.",
					});
				} else {
					setOutcome({
						status: "error",
						error: error?.message || "The Use Cases decision could not be saved.",
						conflict: null,
						response: null,
						announcement: generateTests
							? "The approval outcome could not be confirmed. Check Test Cases generation status."
							: "The review decision was not saved.",
					});
				}
				return null;
			} finally {
				if (activeRequestRef.current.sequence === sequence) {
					submittingRef.current = false;
					activeRequestRef.current = { ...activeRequestRef.current, phase: "idle" };
				}
			}
		},
		[baseProjectRevision, comment, decision, identity, projectId, projectScope, requestScope, snapshotId]
	);

	const reloadLatest = useCallback(async () => {
		if (submittingRef.current || typeof callbacksRef.current.onReload !== "function") {
			return null;
		}
		submittingRef.current = true;
		const reloadProjectScope = projectScopeRef.current;
		setOutcome((current) => ({ ...current, status: "reloading", error: "", announcement: "Reloading latest Use Cases…" }));
		try {
			const project = await callbacksRef.current.onReload();
			if (projectScopeRef.current !== reloadProjectScope) {
				return null;
			}
			if (!project) {
				throw new Error("The latest Use Cases could not be loaded. Try again.");
			}
			retryIdentityRef.current = null;

			setOutcome({
				status: "idle",
				error: "",
				conflict: null,
				response: null,
				announcement: "Latest Use Cases loaded. Review them before submitting again.",
			});
			return project;
		} catch (error) {
			if (projectScopeRef.current !== reloadProjectScope) {
				return null;
			}
			setOutcome((current) => ({
				...current,
				status: current.conflict ? "conflict" : current.response ? "refresh_error" : "error",
				error: error?.message || "The latest Use Cases could not be loaded.",
				announcement: "The latest Use Cases could not be loaded.",
			}));
			return null;
		} finally {
			submittingRef.current = false;
		}
	}, []);

	return {
		recovery,
		decision,
		setDecision,
		comment,
		setComment,
		status: outcome.status,
		error: outcome.error,
		conflict: outcome.conflict,
		response: outcome.response,
		announcement: outcome.announcement,
		isSubmitting: outcome.status === "submitting",
		isReloading: outcome.status === "reloading",
		submit,
		retry: () => submit(lastSubmissionRef.current),
		reloadLatest,
	};
}
