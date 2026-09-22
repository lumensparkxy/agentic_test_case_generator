import { useEffect, useRef, useState } from "react";
import { createRequestId, parseApiError } from "../services/apiClient";

// A receipt always wins over transport state: losing a response is not failure.
export default function useImpactApplication({ project, identity, enabled, apiRequest, captureScope, isCurrent, receiveProject }) {
	const analysisId = project?.current_snapshots?.impact_analysis?.snapshot_id;
	const key = `${identity}:${project?.project_id || ""}:${analysisId || ""}`;
	const marker = `impact-application:${key}`;
	const [local, setLocal] = useState({});
	const active = useRef(new Set());
	const checking = useRef(new Set());
	const emptyChecks = useRef(new Map());
	const latest = useRef();
	latest.current = { project, key, apiRequest, captureScope, isCurrent, receiveProject };
	const operation = analysisId && project?.impact_application?.analysis_snapshot_id === analysisId ? project.impact_application : null;
	const pending = Boolean(analysisId && sessionStorage.getItem(marker));
	const transient = local.key === key ? local : {};
	const state = operation?.status === "applied" ? "applied" : transient.state || operation?.status || (pending ? "checking" : "ready");

	const checkStatus = async ({ rejectedError, observeOnly = false } = {}) => {
		const context = latest.current;
		const scope = context.captureScope();
		if (!scope || !analysisId || checking.current.has(key)) return;
		checking.current.add(key);
		try {
			const response = await context.apiRequest(`/projects/${encodeURIComponent(context.project.project_id)}`);
			if (!response.ok) throw new Error("Could not check application status.");
			const data = await response.json();
			if (latest.current.key !== key || !context.isCurrent(scope)) return;
			if (data.project_id !== context.project.project_id) throw new Error("Project response did not match.");
			const saved = data.impact_application;
			if (active.current.has(key) && saved?.status === "failed" && saved.started_at === context.project.impact_application?.started_at) {
				setLocal({ key, state: "submitting" });
				return;
			}
			context.receiveProject(data, scope);
			if (saved && ["applied", "failed", "verification_required"].includes(saved.status)) {
				sessionStorage.removeItem(marker);
				setLocal({ key });
			} else if (observeOnly && !saved && !pending && !active.current.has(key)) {
				setLocal({ key });
			} else if (rejectedError && !saved) {
				sessionStorage.removeItem(marker);
				setLocal({ key, state: "ready", error: rejectedError });
			} else {
				const count = saved ? 0 : (emptyChecks.current.get(key) || 0) + 1;
				emptyChecks.current.set(key, count);
				setLocal({ key, state: saved?.status === "applying" ? "applying" : count >= 3 ? "unreachable" : "checking" });
			}
		} catch {
			if (latest.current.key === key && context.isCurrent(scope)) setLocal({ key, state: "unreachable" });
		} finally {
			checking.current.delete(key);
		}
	};
	const checkRef = useRef(checkStatus);
	checkRef.current = checkStatus;
	useEffect(() => {
		if (!enabled || !["applying", "submitting", "checking"].includes(state)) return;
		const timer = window.setTimeout(() => void checkRef.current(), 2000);
		return () => window.clearTimeout(timer);
	}, [key, state, local, enabled]);

	useEffect(() => {
		if (!enabled || !analysisId) return;
		const onFocus = () => void checkRef.current({ observeOnly: true });
		const onVisible = () => {
			if (document.visibilityState === "visible") onFocus();
		};
		window.addEventListener("focus", onFocus);
		document.addEventListener("visibilitychange", onVisible);
		return () => {
			window.removeEventListener("focus", onFocus);
			document.removeEventListener("visibilitychange", onVisible);
		};
	}, [key, enabled, analysisId]);

	const apply = async (ids) => {
		const context = latest.current;
		const scope = context.captureScope();
		if (!scope || !analysisId || !ids.length || active.current.has(key) || !["ready", "failed"].includes(state)) return;
		active.current.add(key);
		sessionStorage.setItem(marker, "pending");
		setLocal({ key, state: "submitting" });
		try {
			const response = await context.apiRequest(`/projects/${encodeURIComponent(context.project.project_id)}/impact-update/apply`, {
				method: "POST",
				headers: { "Content-Type": "application/json", "X-Request-ID": createRequestId() },
				body: JSON.stringify({
					analysis_snapshot_id: analysisId,
					accepted_recommendation_ids: ids,
					base_project_revision: context.project.current_revision,
				}),
			});
			if (!response.ok) {
				const error = new Error(await parseApiError(response, "Unable to apply recommendations."));
				error.rejected = response.status >= 400 && response.status < 500;
				throw error;
			}
			const data = await response.json();
			if (latest.current.key !== key || !context.isCurrent(scope)) return;
			if (data.project_id !== context.project.project_id) throw new Error("Project response did not match.");
			context.receiveProject(data, scope);
			if (["applied", "failed", "verification_required"].includes(data.impact_application?.status)) {
				sessionStorage.removeItem(marker);
				setLocal({ key });
			} else setLocal({ key, state: "checking" });
		} catch (error) {
			if (latest.current.key !== key || !context.isCurrent(scope)) return;
			if (error.rejected) {
				sessionStorage.removeItem(marker);
				setLocal({ key, error: `Impact update failed: ${error.message}` });
			} else setLocal({ key, state: "checking" });
			await checkStatus({ rejectedError: error.rejected ? `Impact update failed: ${error.message}` : null });
		} finally {
			active.current.delete(key);
		}
	};
	return { state, operation, error: transient.error, apply, checkStatus };
}
