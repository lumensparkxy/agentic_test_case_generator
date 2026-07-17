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

const reviewFingerprint = ({ identity, projectId, snapshotId, baseProjectRevision, decision, comment }) =>
	JSON.stringify({
		identity,
		projectId,
		snapshotId,
		baseProjectRevision,
		decision,
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
} = {}) {
	const requestRef = useRef(request);
	const callbacksRef = useRef({ onCommitted, onReload });
	const activeRequestRef = useRef({ sequence: 0, scope: "", phase: "idle" });
	const retryIdentityRef = useRef(null);
	const submittingRef = useRef(false);
	const projectScopeRef = useRef("");
	const requestScopeRef = useRef("");
	const scopeContextRef = useRef(null);
	const [decision, setDecisionState] = useState("approve");
	const [comment, setCommentState] = useState("");
	const [outcome, setOutcome] = useState(initialOutcome);

	requestRef.current = request;
	callbacksRef.current = { onCommitted, onReload };
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
			setDecisionState("approve");
			setCommentState("");
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

	const setDecision = useCallback((nextDecision) => {
		setDecisionState(nextDecision);
		retryIdentityRef.current = null;
		setOutcome((current) => (current.status === "error" ? { ...current, error: "", status: "idle" } : current));
	}, []);

	const setComment = useCallback((nextComment) => {
		setCommentState(nextComment);
		retryIdentityRef.current = null;
		setOutcome((current) => (current.status === "error" ? { ...current, error: "", status: "idle" } : current));
	}, []);

	const submit = useCallback(async () => {
		if (submittingRef.current) {
			return null;
		}
		const normalizedComment = `${comment || ""}`.trim();
		if (decision === "request_changes" && !normalizedComment) {
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
			decision,
			comment: normalizedComment,
		});
		if (retryIdentityRef.current?.fingerprint !== fingerprint) {
			retryIdentityRef.current = { fingerprint, requestId: createRequestId() };
		}
		const requestId = retryIdentityRef.current.requestId;
		const isCurrent = () =>
			activeRequestRef.current.sequence === sequence &&
			activeRequestRef.current.scope === scope &&
			projectScopeRef.current === projectScope &&
			requestScopeRef.current === scope;
		const isProjectCurrent = () => activeRequestRef.current.sequence === sequence && projectScopeRef.current === projectScope;

		submittingRef.current = true;
		setOutcome({ status: "submitting", error: "", conflict: null, response: null, announcement: "Saving review decision…" });
		try {
			const response = await submitUseCaseReviewDecision(requestRef.current, {
				projectId,
				snapshotId,
				baseProjectRevision,
				decision,
				comment: normalizedComment,
				requestId,
			});
			if (!isCurrent()) {
				return null;
			}
			retryIdentityRef.current = null;
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
					decision,
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
						: decision === "approve"
							? "Use Cases approved."
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
					announcement: "The review decision was not saved.",
				});
			}
			return null;
		} finally {
			if (activeRequestRef.current.sequence === sequence) {
				submittingRef.current = false;
				activeRequestRef.current = { ...activeRequestRef.current, phase: "idle" };
			}
		}
	}, [baseProjectRevision, comment, decision, identity, projectId, projectScope, requestScope, snapshotId]);

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
		retry: submit,
		reloadLatest,
	};
}
