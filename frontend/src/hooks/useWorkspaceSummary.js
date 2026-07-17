import { useCallback, useEffect, useRef, useState } from "react";

import { fetchWorkspaceSummary } from "../services/workspaceSummaryClient";

const INITIAL_STATE = Object.freeze({
	identity: "",
	summary: null,
	status: "idle",
	error: "",
});

const isAbortError = (error) => error?.name === "AbortError";

export default function useWorkspaceSummary({ request, enabled = true, identity = "" } = {}) {
	const requestRef = useRef(request);
	const activeRequestRef = useRef({ controller: null, sequence: 0 });
	const [state, setState] = useState(INITIAL_STATE);
	requestRef.current = request;

	const refresh = useCallback(async () => {
		if (!enabled || typeof requestRef.current !== "function") {
			return null;
		}

		activeRequestRef.current.controller?.abort();
		const controller = new AbortController();
		const sequence = activeRequestRef.current.sequence + 1;
		activeRequestRef.current = { controller, sequence };
		setState((current) => ({
			identity,
			summary: current.identity === identity ? current.summary : null,
			status: current.identity === identity && current.summary ? "refreshing" : "loading",
			error: "",
		}));

		try {
			const summary = await fetchWorkspaceSummary(requestRef.current, { signal: controller.signal });
			if (activeRequestRef.current.sequence !== sequence || controller.signal.aborted) {
				return null;
			}
			setState({ identity, summary, status: "success", error: "" });
			return summary;
		} catch (error) {
			if (activeRequestRef.current.sequence !== sequence || controller.signal.aborted || isAbortError(error)) {
				return null;
			}
			setState((current) => ({
				identity,
				summary: current.identity === identity ? current.summary : null,
				status: "error",
				error: error?.message || "Workspace summary is unavailable. Please try again.",
			}));
			return null;
		}
	}, [enabled, identity]);

	const clear = useCallback(() => {
		activeRequestRef.current.controller?.abort();
		activeRequestRef.current = {
			controller: null,
			sequence: activeRequestRef.current.sequence + 1,
		};
		setState(INITIAL_STATE);
	}, []);

	useEffect(() => {
		clear();
		if (!enabled) {
			return undefined;
		}
		void refresh();
		return () => {
			activeRequestRef.current.controller?.abort();
		};
	}, [clear, enabled, identity, refresh]);

	const visibleState = state.identity === identity ? state : INITIAL_STATE;

	return {
		summary: visibleState.summary,
		status: visibleState.status,
		error: visibleState.error,
		isLoading: visibleState.status === "idle" || visibleState.status === "loading",
		isRefreshing: visibleState.status === "refreshing",
		refresh,
		retry: refresh,
		clear,
	};
}
