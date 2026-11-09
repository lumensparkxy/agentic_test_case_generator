import { useCallback, useEffect, useRef, useState } from "react";
import useReviewDraft from "./useReviewDraft";
import { API_CONTRACT_ENDPOINTS } from "../api/generated/api-contracts";

const POLL_DELAY_MS = 2000;
const MAX_AUTOMATIC_CHECKS = 10;
const ACTIVE_STATES = ["queued", "running"];

export default function useApprovalContinuation({ enabled, projectId, identity, request }) {
	const scope = enabled ? JSON.stringify([identity, projectId]) : "";
	const projectSegment = encodeURIComponent(projectId || "");
	const basePath = API_CONTRACT_ENDPOINTS.approvalContinuations.path.replace("{project_id}", projectSegment);
	const pending = useReviewDraft({
		identity: enabled ? identity : "",
		projectId,
		snapshotId: "scope-approval-v1",
		name: "pending-scope-approval",
		initialValue: null,
	});
	const requestRef = useRef(request);
	requestRef.current = request;
	const activeScope = useRef(scope);
	activeScope.current = scope;
	const inFlight = useRef(null);
	const pollCount = useRef(0);
	const cancelInFlight = useRef(null);
	const [readState, setReadState] = useState(null);
	const [actionState, setActionState] = useState(null);
	const current = readState?.scope === scope ? readState : null;
	const action = actionState?.scope === scope ? actionState : null;
	const runs = (current?.data?.runs || []).filter((item) => item.stage === "prepare_scenarios");
	const requestId = pending.value?.requestId || "";
	const match = requestId && runs.find((item) => item.request_id === requestId);
	const receipt = requestId && current?.data?.receipt?.request_id === requestId ? current.data.receipt : null;
	const run = match || (!requestId ? runs[0] : null);
	const unresolved = Boolean(requestId && !match && !receipt);

	const refresh = useCallback(
		async ({ reset = false } = {}) => {
			if (!scope || inFlight.current?.scope === scope) return;
			if (reset) pollCount.current = 0;
			const controller = new AbortController();
			const read = { scope, controller };
			inFlight.current = read;
			const path = requestId ? `${basePath}?${new URLSearchParams({ request_id: requestId })}` : basePath;
			try {
				const response = await requestRef.current(path, { method: "GET", signal: controller.signal });
				if (!response.ok) throw new Error("Scenario preparation status is unavailable.");
				const data = await response.json();
				if (!controller.signal.aborted && activeScope.current === scope) setReadState({ scope, data, error: "" });
			} catch {
				if (!controller.signal.aborted && activeScope.current === scope)
					setReadState((previous) => ({
						scope,
						data: previous?.scope === scope ? previous.data : null,
						error: "Could not check scenario preparation. Check status again.",
					}));
			} finally {
				if (inFlight.current === read) inFlight.current = null;
			}
		},
		[basePath, requestId, scope]
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

	const accept = (response) => {
		const result = response.continuation;
		if (result) {
			setReadState((previous) => ({
				scope,
				error: "",
				data: { enabled: true, runs: [result, ...(previous?.data?.runs || []).filter((item) => item.run_id !== result.run_id)] },
			}));
		}
		setActionState({ scope, error: "", blockedReason: response.blocked_reason || "" });
		pending.clear();
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
			if (!response.ok) throw new Error("Could not cancel scenario preparation. Check the current status.");
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

	return {
		available: current?.data?.enabled === true,
		run,
		active: ACTIVE_STATES.includes(run?.state),
		unresolved,
		receipt,
		blockedReason: action?.blockedReason || receipt?.blocked_reason || "",
		error: action?.error || current?.error || pending.error,
		pendingRequest: pending.value,
		cancelling: action?.cancelling === true,
		begin: (value) => {
			setActionState(null);
			pending.setValue(value);
		},
		clearPending: () => pending.clear(),
		accept,
		cancel,
		refresh,
	};
}
